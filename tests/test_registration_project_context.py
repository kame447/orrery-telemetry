from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTER_LIB = ROOT / "bin" / "lib" / "agentstack-register.sh"
PROJECT_CONTEXT = ROOT / "hooks" / "project-context.sh"
ZSH = shutil.which("zsh")


def _bash(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=ROOT,
        env=run_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_registration_allows_project_namespace_outside_target_repository(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target"
    namespace = tmp_path / "coordination-project"
    target.mkdir()
    namespace.mkdir()
    for repository in (target, namespace):
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
    calls = tmp_path / "calls"
    script = f'''
source "{REGISTER_LIB}"
ags_mcp_call() {{
  local tool="$1"; shift
  printf '%s|%s\\n' "$tool" "$*" >> "$CALLS"
  case "$tool" in
    whois)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":7,"name":"Child","program":"claude-code","project_id":1}}}}}}'
      ;;
    ensure_project)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":1}}}}}}'
      ;;
    register_agent)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":7,"name":"Child","program":"claude-code","project_id":1,"registration_token":"reserved-token"}}}}}}'
      ;;
    *) return 1 ;;
  esac
}}
ags_store_registration_token() {{ :; }}
ags_apply_contact_policy() {{ :; }}
CHILD_REGISTRATION_TOKEN=reserved-token
export CHILD_REGISTRATION_TOKEN
ags_register_session "$NAMESPACE" claude-code model cc "$TARGET" Child reserved >/dev/null
status=$?
printf 'status=%s registered=%s\\n' "$status" "$AGS_REGISTERED_AGENT_NAME"
'''
    result = _bash(
        script,
        {"TARGET": str(target), "NAMESPACE": str(namespace), "CALLS": str(calls)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "status=0 registered=Child"
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert [line.split("|", 1)[0] for line in lines] == [
        "whois",
        "register_agent",
    ]
    assert f"project_key={namespace}" in lines[0]
    assert f"project_key={namespace}" in lines[1]
    assert "existing_agent_id=7" in lines[1]
    assert "refresh_existing=true" in lines[1]


@pytest.mark.parametrize("response_token", ["reserved-token", None, "unexpected-response-token"])
@pytest.mark.parametrize("program", ["claude-code", "codex", "codex-cli"])
def test_reserved_registration_checks_existing_identity_then_authenticates_on_register(
    tmp_path: pathlib.Path, response_token: str | None, program: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    calls = tmp_path / "calls"
    script = f'''
source "{REGISTER_LIB}"
ags_mcp_call() {{
  local tool="$1"; shift
  printf '%s|%s\\n' "$tool" "$*" >> "$CALLS"
  case "$tool" in
    whois)
      [[ "$*" != *registration_token=* ]] || return 1
      printf '%s\\n' "$WHOIS_RESPONSE"
      ;;
    ensure_project)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":1}}}}}}'
      ;;
    register_agent)
      [[ "$*" == *registration_token=reserved-token* ]] || return 1
      printf '%s\\n' "$REGISTER_RESPONSE"
      ;;
    *)
      return 1
      ;;
  esac
}}
ags_store_registration_token() {{ :; }}
ags_apply_contact_policy() {{ :; }}
CHILD_REGISTRATION_TOKEN=reserved-token
export CHILD_REGISTRATION_TOKEN
ags_register_session "$TARGET" "$PROGRAM" model cc "$TARGET" Child reserved >/dev/null
status=$?
printf 'status=%s registered=%s token=%s\\n' "$status" "$AGS_REGISTERED_AGENT_NAME" "$AGS_REGISTERED_REGISTRATION_TOKEN"
'''
    import json

    response = {"id": 7, "name": "Child", "program": program, "project_id": 1}
    whois_response = json.dumps({"result": {"structuredContent": dict(response)}})
    if response_token is not None:
        response["registration_token"] = response_token
    result = _bash(script, {
        "TARGET": str(target), "CALLS": str(calls),
        "PROGRAM": program, "WHOIS_RESPONSE": whois_response,
        "REGISTER_RESPONSE": json.dumps({"result": {"structuredContent": response}}),
    })
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "status=0 registered=Child token=reserved-token"
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert [line.split("|", 1)[0] for line in lines] == [
        "whois",
        "register_agent",
    ]
    assert "registration_token=" not in lines[0]
    assert "registration_token=reserved-token" in lines[1]
    assert "existing_agent_id=7" in lines[1]
    assert "refresh_existing=true" in lines[1]


def test_reserved_registration_refuses_missing_identity_before_mutation(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    calls = tmp_path / "calls"
    script = f'''
source "{REGISTER_LIB}"
ags_mcp_call() {{
  local tool="$1"; shift
  printf '%s\\n' "$tool" >> "$CALLS"
  case "$tool" in
    whois)
      printf '%s\\n' '{{"result":{{"isError":true,"content":[{{"type":"text","text":"not found"}}]}}}}'
      ;;
    *)
      return 1
      ;;
  esac
}}
CHILD_REGISTRATION_TOKEN=foreign-project-token
export CHILD_REGISTRATION_TOKEN
ags_register_session "$TARGET" claude-code model cc "$TARGET" Child reserved >/dev/null
status=$?
printf 'status=%s\\n' "$status"
'''
    result = _bash(script, {"TARGET": str(target), "CALLS": str(calls)})
    assert result.returncode == 0
    assert result.stdout.strip() == "status=1"
    assert calls.read_text(encoding="utf-8").splitlines() == ["whois"]
    assert "cannot confirm reserved identity" in result.stderr


def test_logical_project_key_rebinds_workspace_provenance_without_rejecting_namespace(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target"
    other = tmp_path / "other"
    target.mkdir()
    other.mkdir()
    script = f'''
source "{PROJECT_CONTEXT}"
agentstack_validate_project_context "$TARGET" logical-project
'''
    result = _bash(
        script,
        {
            "TARGET": str(target),
            "AGENTSTACK_PROJECT_KEY": "logical-project",
            "AGENTSTACK_PROJECT_WORK_DIR": str(other),
            "AGENTSTACK_PROJECT_REPOSITORY": str(other),
        },
    )
    assert result.returncode == 0, result.stderr

    import json

    context = json.loads(result.stdout)
    assert context["project_key"] == "logical-project"
    assert context["work_dir"] == str(target)
    assert context["repository_key"] is None
    # Protected-root policy is owned by launch preparation, not registration.


@pytest.mark.skipif(ZSH is None, reason="zsh is not installed")
def test_register_library_loads_project_validator_when_sourced_from_zsh() -> None:
    result = subprocess.run(
        [
            ZSH,
            "-c",
            f'set -eu; source "{REGISTER_LIB}"; command -v agentstack_validate_project_context',
        ],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "agentstack_validate_project_context" in result.stdout


@pytest.mark.skipif(ZSH is None, reason="zsh is not installed")
def test_reserved_registration_runs_when_library_is_sourced_from_zsh(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    script = f'''
set -eu
source "{REGISTER_LIB}"
ags_mcp_call() {{
  local tool="$1"; shift
  case "$tool" in
    whois)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":7,"name":"Child","program":"claude-code","project_id":1}}}}}}'
      ;;
    ensure_project)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":1}}}}}}'
      ;;
    register_agent)
      printf '%s\\n' '{{"result":{{"structuredContent":{{"id":7,"name":"Child","program":"claude-code","project_id":1,"registration_token":"reserved-token"}}}}}}'
      ;;
    *) return 1 ;;
  esac
}}
ags_store_registration_token() {{ :; }}
ags_apply_contact_policy() {{ :; }}
CHILD_REGISTRATION_TOKEN=reserved-token
export CHILD_REGISTRATION_TOKEN
ags_register_session logical-project claude-code model cc "$TARGET" Child reserved >/dev/null
printf 'registered=%s\\n' "$AGS_REGISTERED_AGENT_NAME"
'''
    env = os.environ.copy()
    env["TARGET"] = str(target)
    result = subprocess.run(
        [ZSH, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.strip() == "registered=Child"


@pytest.mark.parametrize("failure", [
    "missing-target", "broken-repository", "broken-parent-repository",
    "empty-namespace", "missing-validator", "missing-token",
])
def test_registration_local_failures_make_no_mail_calls(
    tmp_path: pathlib.Path, failure: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    if failure == "missing-target":
        target = target / "does-not-exist"
    elif failure in ("broken-repository", "broken-parent-repository"):
        (target / ".git").write_text("gitdir: /missing/registration-metadata\n")
        if failure == "broken-parent-repository":
            target = target / "child"
            target.mkdir()
    calls = tmp_path / "calls"
    script = f'''
set -eu
source "{REGISTER_LIB}"
ags_mcp_call() {{ printf '%s\\n' "$1" >> "$CALLS"; return 1; }}
ags_load_registration_token() {{ return 1; }}
if [[ "$FAILURE" == missing-validator ]]; then
  unset -f agentstack_validate_project_context
fi
CHILD_REGISTRATION_TOKEN=reserved-token
[[ "$FAILURE" != missing-token ]] || CHILD_REGISTRATION_TOKEN=""
namespace=logical-project
[[ "$FAILURE" != empty-namespace ]] || namespace=""
if ags_register_session "$namespace" codex model cx "$TARGET" Child reserved >/dev/null; then
  exit 99
fi
printf 'registered=%s\\n' "$AGS_REGISTERED_AGENT_NAME"
'''
    result = _bash(script, {
        "TARGET": str(target), "CALLS": str(calls), "FAILURE": failure,
    })
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "registered="
    assert not calls.exists(), calls.read_text() if calls.exists() else ""


@pytest.mark.parametrize("response", [
    "", "not-json", '{"result":{}}',
    '{"result":{"isError":true,"content":[{"type":"text","text":"unavailable"}]}}',
])
def test_reserved_registration_refuses_unknown_whois_before_mutation(
    tmp_path: pathlib.Path, response: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    calls = tmp_path / "calls"
    script = f'''
set -eu
source "{REGISTER_LIB}"
ags_mcp_call() {{
  printf '%s\\n' "$1" >> "$CALLS"
  [[ "$1" == whois ]] || return 99
  printf '%s' "$WHOIS_RESPONSE"
}}
CHILD_REGISTRATION_TOKEN=reserved-token
if ags_register_session logical-project codex model cx "$TARGET" Child reserved >/dev/null; then
  exit 99
fi
printf 'registered=%s\\n' "$AGS_REGISTERED_AGENT_NAME"
'''
    result = _bash(script, {
        "TARGET": str(target), "CALLS": str(calls), "WHOIS_RESPONSE": response,
    })
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "registered="
    assert calls.read_text().splitlines() == ["whois"]


def test_registration_validation_does_not_replace_custom_protected_roots(
    tmp_path: pathlib.Path,
) -> None:
    roots = f"{tmp_path}/shared-vault:{tmp_path}/another-project"
    script = f'''
set -eu
source "{PROJECT_CONTEXT}"
agentstack_validate_project_context "$TARGET" logical-project >/dev/null
printf '%s\\n' "$AGENTSTACK_PROTECTED_ROOTS"
'''
    result = _bash(script, {"TARGET": str(tmp_path), "AGENTSTACK_PROTECTED_ROOTS": roots})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == roots


@pytest.mark.parametrize("field,value", [
    ("id", 0), ("id", -1), ("id", True), ("id", "7"),
    ("project_id", 0), ("project_id", True),
    ("name", "AnotherChild"), ("program", "claude-code"),
])
def test_reserved_whois_requires_exact_typed_identity_before_refresh(
    tmp_path: pathlib.Path, field: str, value: object,
) -> None:
    import json

    identity = {"id": 7, "project_id": 1, "name": "Child", "program": "codex"}
    identity[field] = value
    calls = tmp_path / "calls"
    script = f'''
set -eu
source "{REGISTER_LIB}"
ags_mcp_call() {{ printf '%s\\n' "$1" >> "$CALLS"; printf '%s' "$RESPONSE"; }}
CHILD_REGISTRATION_TOKEN=owner-token
if ags_register_session logical-project codex model cx "$TARGET" Child reserved >/dev/null; then exit 99; fi
printf 'registered=%s\\n' "$AGS_REGISTERED_AGENT_NAME"
'''
    result = _bash(script, {"TARGET": str(tmp_path), "CALLS": str(calls),
                            "RESPONSE": json.dumps({"result": {"structuredContent": identity}})})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "registered="
    assert calls.read_text().splitlines() == ["whois"]


@pytest.mark.parametrize("failure", [
    "null-owner", "wrong-token", "unsupported-schema",
    "readback-id", "readback-project", "readback-program",
])
def test_reserved_refresh_never_falls_back_or_saves_unconfirmed_identity(
    tmp_path: pathlib.Path, failure: str,
) -> None:
    import json

    row = {"id": 7, "project_id": 1, "name": "Child", "program": "codex"}
    response = dict(row)
    if failure in ("null-owner", "wrong-token", "unsupported-schema"):
        response = {"isError": True, "content": [{"type": "text", "text": failure}]}
    else:
        field = {"readback-id": "id", "readback-project": "project_id", "readback-program": "program"}[failure]
        response[field] = "claude-code" if field == "program" else 999
        response = {"structuredContent": response}
    calls = tmp_path / "calls"
    script = f'''
set -eu
source "{REGISTER_LIB}"
ags_mcp_call() {{
  local tool="$1"; shift
  printf '%s|%s\\n' "$tool" "$*" >> "$CALLS"
  case "$tool" in
    whois) printf '%s' "$WHOIS_RESPONSE" ;;
    register_agent)
      [[ "$*" == *existing_agent_id=7* && "$*" == *refresh_existing=true* ]] || return 99
      printf '%s' "$REGISTER_RESPONSE" ;;
    *) return 99 ;;
  esac
}}
ags_store_registration_token() {{ printf 'store\\n' >> "$CALLS"; }}
ags_apply_contact_policy() {{ printf 'contact-policy\\n' >> "$CALLS"; }}
CHILD_REGISTRATION_TOKEN=owner-token
if ags_register_session logical-project codex model cx "$TARGET" Child reserved >/dev/null; then exit 99; fi
printf 'registered=%s\\n' "$AGS_REGISTERED_AGENT_NAME"
'''
    result = _bash(script, {"TARGET": str(tmp_path), "CALLS": str(calls),
                            "WHOIS_RESPONSE": json.dumps({"result": {"structuredContent": row}}),
                            "REGISTER_RESPONSE": json.dumps({"result": response})})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "registered="
    made = calls.read_text().splitlines()
    assert [call.split("|", 1)[0] for call in made] == ["whois", "register_agent"]
    assert "registration_token=" not in made[0]
    assert "owner-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("mode,name", [("reserved", ""), ("unknown", "Child"), ("", "Child"), ("omitted", "")])
def test_registration_rejects_missing_reserved_name_and_unknown_mode_without_mail(
    tmp_path: pathlib.Path, mode: str, name: str,
) -> None:
    calls = tmp_path / "calls"
    script = f'''
set -eu
source "{REGISTER_LIB}"
ags_mcp_call() {{ printf '%s\\n' "$1" >> "$CALLS"; return 99; }}
if [[ "$MODE" == omitted ]]; then
  if ags_register_session logical-project codex model cx "$TARGET" >/dev/null; then exit 99; fi
else
  if ags_register_session logical-project codex model cx "$TARGET" "$NAME" "$MODE" >/dev/null; then exit 99; fi
fi
printf 'registered=%s reason=%s\\n' "$AGS_REGISTERED_AGENT_NAME" "$AGS_REGISTRATION_DIAG_REASON"
'''
    result = _bash(script, {"TARGET": str(tmp_path), "CALLS": str(calls), "MODE": mode, "NAME": name})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "registered= reason=input-missing"
    assert not calls.exists()
