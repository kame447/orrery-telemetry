from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTER_LIB = ROOT / "bin" / "lib" / "agentstack-register.sh"
PYTHON = Path(sys.executable)


def _run(script: str, *, env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env.update(env)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        check=check,
    )


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "phase4@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Phase Four"], check=True)
    (path / "tracked.txt").write_text("phase4\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return path


def _base_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    tmux_runtime = tmp_path / "tmux"
    home.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    tmux_runtime.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "AGENTSTACK_RUNTIME_DIR": str(runtime),
        "AGENTSTACK_PYTHON": str(PYTHON),
        "AGENTSTACK_PROJECT_CONTEXT_LIB": str(ROOT / "hooks" / "project-context.sh"),
        "AGENTSTACK_PROJECT_KEY": "",
        "PROJECT_KEY": "",
        "AGENTSTACK_PROJECT_CONTEXT": "",
        "AGENTSTACK_PROJECT_CONTEXT_JSON": "",
        "AGENT_NAME": "",
        "PARENT_AGENT": "",
        "CHILD_REGISTRATION_TOKEN": "",
        "AGENTSTACK_RESERVED_IDENTITY": "",
        "GIT_DIR": "",
        "GIT_WORK_TREE": "",
        "GIT_COMMON_DIR": "",
        "TMUX": "",
        "TMUX_PANE": "",
        "TMUX_TMPDIR": str(tmux_runtime),
        "CALLS": str(tmp_path / "calls"),
        "PROJECT_CALLS": str(tmp_path / "project-calls"),
    }


FAKE_MAIL = r'''
ags_mcp_call() {
  local tool="$1"; shift
  printf '%s\n' "$tool" >> "$CALLS"
  for arg in "$@"; do
    case "$arg" in
      project_key=*|human_key=*) printf '%s:%s\n' "$tool" "$arg" >> "$PROJECT_CALLS" ;;
    esac
  done
  case "$tool" in
    whois)
      printf '%s\n' '{"result":{"structuredContent":{"name":"PhaseCurie"}}}' ;;
    ensure_project)
      if [[ "${FAIL_ENSURE:-0}" == "1" ]]; then
        printf '%s\n' '{"result":{"isError":true,"content":[{"type":"text","text":"boom"}]}}'
      else
        printf '%s\n' '{"result":{"structuredContent":{"id":1}}}'
      fi ;;
    register_agent)
      printf '%s\n' '{"result":{"structuredContent":{"id":2,"name":"PhaseCurie","registration_token":"phase-token"}}}' ;;
    *) return 1 ;;
  esac
}
ags_agent_name_status() { printf '%s\n' available; }
ags_generate_registration_token() { printf '%s\n' phase-token; }
ags_apply_contact_policy() { :; }
'''


def _register_explicit(repo: Path, namespace: str, env: dict[str, str]) -> None:
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
context="$(agentstack_resolve_invocation_context "$TARGET" "$NAMESPACE")"
transport="$(agentstack_build_invocation_transport "$context" "$NAMESPACE")"
ags_register_session "$NAMESPACE" codex model cx "$TARGET" PhaseCurie candidate "$transport" phase4-test >/dev/null
'''
    _run(script, env={**env, "TARGET": str(repo), "NAMESPACE": namespace})


def test_success_publishes_token_bound_strong_owner_record(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    env["AGS_REGISTRATION_PROJECT_KEY"] = "stale-internal-project"
    namespace = "human-namespace"

    _register_explicit(repo, namespace, env)

    owner_path = Path(env["AGENTSTACK_RUNTIME_DIR"]) / "agent_owner_PhaseCurie.json"
    token_path = Path(env["AGENTSTACK_RUNTIME_DIR"]) / "agent_token_PhaseCurie"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    assert owner == {
        "schema": 1,
        "agent_name": "PhaseCurie",
        "name_key": "phasecurie",
        "project_key": namespace,
        "repository_key": str(repo.resolve()),
        "non_git_root": None,
        "created_by": "phase4-test",
        "token_sha256": hashlib.sha256(b"phase-token").hexdigest(),
        "updated_at": owner["updated_at"],
    }
    assert token_path.read_text(encoding="utf-8") == "phase-token"
    assert stat.S_IMODE(owner_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert list(owner_path.parent.glob("*.pending")) == []
    project_calls = Path(env["PROJECT_CALLS"]).read_text(encoding="utf-8")
    assert "stale-internal-project" not in project_calls
    assert "ensure_project:human_key=human-namespace" in project_calls
    assert "register_agent:project_key=human-namespace" in project_calls


def test_strong_owner_allows_linked_worktree_but_rejects_independent_clone_before_mail(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-qb", "phase4-linked", str(linked)],
        check=True,
    )
    other = _git_repo(tmp_path / "other")
    env = _base_env(tmp_path)
    _register_explicit(repo, "custom-mail-space", env)
    Path(env["CALLS"]).write_text("", encoding="utf-8")

    resume = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=phase-token
export CHILD_REGISTRATION_TOKEN
ags_register_session custom-mail-space codex model cx "$TARGET" PhaseCurie reserved >/dev/null
'''
    _run(resume, env={**env, "TARGET": str(linked)})
    assert Path(env["CALLS"]).read_text(encoding="utf-8").splitlines() == [
        "ensure_project",
        "register_agent",
    ]

    Path(env["CALLS"]).write_text("", encoding="utf-8")
    refused = _run(resume, env={**env, "TARGET": str(other)}, check=False)
    assert refused.returncode != 0
    assert not Path(env["CALLS"]).exists() or Path(env["CALLS"]).read_text(encoding="utf-8") == ""


def test_digest_mismatch_and_failed_ensure_never_publish_or_send_owner_token(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    _register_explicit(repo, "custom", env)
    Path(env["CALLS"]).write_text("", encoding="utf-8")

    bad_resume = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=wrong-token
export CHILD_REGISTRATION_TOKEN
ags_register_session custom codex model cx "$TARGET" PhaseCurie reserved >/dev/null
'''
    refused = _run(bad_resume, env={**env, "TARGET": str(repo)}, check=False)
    assert refused.returncode != 0
    assert not Path(env["CALLS"]).exists() or Path(env["CALLS"]).read_text(encoding="utf-8") == ""

    clean = tmp_path / "clean"
    clean.mkdir()
    clean_env = _base_env(tmp_path / "second")
    failed = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
context="$(agentstack_resolve_invocation_context "$TARGET" explicit)"
transport="$(agentstack_build_invocation_transport "$context" explicit)"
ags_register_session explicit codex model cx "$TARGET" PhaseCurie candidate "$transport" phase4-test >/dev/null
'''
    result = _run(
        failed,
        env={**clean_env, "TARGET": str(clean), "FAIL_ENSURE": "1"},
        check=False,
    )
    assert result.returncode != 0
    runtime = Path(clean_env["AGENTSTACK_RUNTIME_DIR"])
    assert not (runtime / "agent_owner_PhaseCurie.json").exists()
    assert not (runtime / "agent_token_PhaseCurie").exists()
    assert list(runtime.glob("*.pending")) == []


def test_failed_token_install_rolls_back_owner_publication(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
context="$(agentstack_resolve_invocation_context "$TARGET" explicit)"
ags_begin_registration_ownership "$context" PhaseCurie phase-token phase4-test candidate
mkdir "$(ags_registration_token_file PhaseCurie)"
if ags_commit_registration_ownership "$context" PhaseCurie PhaseCurie phase-token phase4-test; then
  exit 99
fi
ags_release_registration_ownership
'''
    result = _run(script, env={**env, "TARGET": str(repo)}, check=False)
    assert result.returncode == 0, result.stderr
    assert not (runtime / "agent_owner_PhaseCurie.json").exists()
    assert list(runtime.glob("*.pending")) == []
    assert list(runtime.glob("*.tmp.*")) == []


def test_same_repository_legacy_owner_authenticates_before_ensure_and_upgrades(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    child_dir = runtime / "child-agents"
    child_dir.mkdir()
    legacy = child_dir / "PhaseCurie.json"
    legacy.write_text(
        json.dumps(
            {
                "agent_name": "PhaseCurie",
                "project_key": str(repo),
                "registration_token": "phase-token",
            }
        ),
        encoding="utf-8",
    )
    legacy.chmod(0o600)

    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=phase-token
export CHILD_REGISTRATION_TOKEN
ags_register_session "" codex model cx "$TARGET" PhaseCurie reserved >/dev/null
'''
    _run(script, env={**env, "TARGET": str(repo)})
    assert Path(env["CALLS"]).read_text(encoding="utf-8").splitlines() == [
        "whois",
        "ensure_project",
        "register_agent",
    ]
    owner = json.loads((runtime / "agent_owner_PhaseCurie.json").read_text(encoding="utf-8"))
    assert owner["repository_key"] == str(repo.resolve())
    assert owner["created_by"] == "legacy-resume"


def test_cross_repository_legacy_and_ambient_namespace_refuse_before_mail(
    tmp_path: Path,
) -> None:
    first = _git_repo(tmp_path / "first")
    second = _git_repo(tmp_path / "second")
    env = _base_env(tmp_path)
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    child_dir = runtime / "child-agents"
    child_dir.mkdir()
    legacy = child_dir / "PhaseCurie.json"
    legacy.write_text(
        json.dumps(
            {
                "agent_name": "PhaseCurie",
                "project_key": str(first),
                "registration_token": "phase-token",
            }
        ),
        encoding="utf-8",
    )
    legacy.chmod(0o600)
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=phase-token
export CHILD_REGISTRATION_TOKEN
ags_register_session "$AMBIENT" codex model cx "$TARGET" PhaseCurie reserved >/dev/null
'''
    refused = _run(
        script,
        env={**env, "TARGET": str(second), "AMBIENT": str(first)},
        check=False,
    )
    assert refused.returncode != 0
    assert not Path(env["CALLS"]).exists() or Path(env["CALLS"]).read_text(encoding="utf-8") == ""


def test_reserved_marker_and_token_without_persisted_provenance_refuse_before_mail(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=phase-token
AGENTSTACK_RESERVED_IDENTITY=1
export CHILD_REGISTRATION_TOKEN AGENTSTACK_RESERVED_IDENTITY
ags_register_session "" codex model cx "$TARGET" PhaseCurie reserved >/dev/null
'''
    refused = _run(script, env={**env, "TARGET": str(repo)}, check=False)
    assert refused.returncode != 0
    assert not Path(env["CALLS"]).exists()
    assert not list(Path(env["AGENTSTACK_RUNTIME_DIR"]).glob("agent_owner_*"))


def test_non_git_strong_owner_is_confined_to_its_persisted_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    child = root / "nested"
    elsewhere = tmp_path / "elsewhere"
    child.mkdir(parents=True)
    elsewhere.mkdir()
    env = _base_env(tmp_path)
    _register_explicit(root, "non-git-custom", env)
    Path(env["CALLS"]).write_text("", encoding="utf-8")

    resume = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=phase-token
export CHILD_REGISTRATION_TOKEN
ags_register_session non-git-custom codex model cx "$TARGET" PhaseCurie reserved >/dev/null
'''
    _run(resume, env={**env, "TARGET": str(child)})
    owner_path = Path(env["AGENTSTACK_RUNTIME_DIR"]) / "agent_owner_PhaseCurie.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    assert owner["repository_key"] is None
    assert owner["non_git_root"] == str(root.resolve())

    Path(env["CALLS"]).write_text("", encoding="utf-8")
    refused = _run(resume, env={**env, "TARGET": str(elsewhere)}, check=False)
    assert refused.returncode != 0
    assert Path(env["CALLS"]).read_text(encoding="utf-8") == ""

    # An explicit top-level transport is corroboration, not a replacement for
    # reserved ownership. Both contexts have repository_key=null, so this is
    # the case that a project/repository-only comparison would wrongly accept.
    transport_bypass = f'''
set -euo pipefail
source "{REGISTER_LIB}"
{FAKE_MAIL}
CHILD_REGISTRATION_TOKEN=phase-token
export CHILD_REGISTRATION_TOKEN
context="$(agentstack_resolve_invocation_context "$TARGET" non-git-custom)"
transport="$(agentstack_build_invocation_transport "$context" non-git-custom)"
ags_register_session non-git-custom codex model cx "$TARGET" PhaseCurie reserved "$transport" >/dev/null
'''
    refused = _run(
        transport_bypass,
        env={**env, "TARGET": str(elsewhere)},
        check=False,
    )
    assert refused.returncode != 0
    assert Path(env["CALLS"]).read_text(encoding="utf-8") == ""


def test_pending_claim_is_atomic_across_host_global_name_aliases(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
context="$(agentstack_resolve_invocation_context "$PROJECT" alias-space)"
ags_begin_registration_ownership "$context" Phase-Curie first-token phase4-test candidate
first_claim="$AGS_REGISTRATION_PENDING_FILE"
if ags_begin_registration_ownership "$context" PhaseCurie second-token phase4-test candidate; then
  exit 99
fi
test "$first_claim" = "$(ags_registration_claim_path PhaseCurie)"
ags_release_registration_ownership
'''
    result = _run(script, env={**env, "PROJECT": str(repo)}, check=False)
    assert result.returncode == 0, result.stderr
    assert list(runtime.glob("*.pending")) == []


def test_delayed_alias_contender_rechecks_durable_owner_under_claim(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
context="$(agentstack_resolve_invocation_context "$PROJECT" alias-space)"
mkfifo "$RUNTIME/ready" "$RUNTIME/go"
(
  # This is the contender's preflight. It passes before the other spelling
  # starts, then deliberately pauses until that owner has been published.
  if ags_local_agent_name_conflicts alias-space PhaseCurie candidate; then
    exit 97
  fi
  printf 'ready\n' > "$RUNTIME/ready"
  IFS= read -r _ < "$RUNTIME/go"
  if ags_begin_registration_ownership "$context" PhaseCurie second-token phase4-test candidate; then
    ags_release_registration_ownership
    exit 98
  fi
) &
contender=$!
IFS= read -r _ < "$RUNTIME/ready"
ags_begin_registration_ownership "$context" Phase-Curie first-token phase4-test candidate
ags_commit_registration_ownership "$context" Phase-Curie Phase-Curie first-token phase4-test
printf 'go\n' > "$RUNTIME/go"
wait "$contender"
'''
    result = _run(
        script,
        env={**env, "PROJECT": str(repo), "RUNTIME": str(runtime)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (runtime / "agent_owner_Phase-Curie.json").exists()
    assert not (runtime / "agent_owner_PhaseCurie.json").exists()
    assert list(runtime.glob("*.pending")) == []


def test_delayed_substitution_rechecks_returned_alias_under_claim(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    script = f'''
set -euo pipefail
source "{REGISTER_LIB}"
context="$(agentstack_resolve_invocation_context "$PROJECT" alias-space)"
mkfifo "$RUNTIME/ready" "$RUNTIME/go"
(
  ags_begin_registration_ownership "$context" RequestedTuring request-token phase4-test candidate
  if ags_local_agent_name_conflicts alias-space PhaseCurie substitution; then
    ags_release_registration_ownership
    exit 97
  fi
  printf 'ready\n' > "$RUNTIME/ready"
  IFS= read -r _ < "$RUNTIME/go"
  if ags_commit_registration_ownership "$context" RequestedTuring PhaseCurie request-token phase4-test; then
    exit 98
  fi
  ags_release_registration_ownership
) &
contender=$!
IFS= read -r _ < "$RUNTIME/ready"
ags_begin_registration_ownership "$context" Phase-Curie owner-token phase4-test candidate
ags_commit_registration_ownership "$context" Phase-Curie Phase-Curie owner-token phase4-test
printf 'go\n' > "$RUNTIME/go"
wait "$contender"
'''
    result = _run(
        script,
        env={**env, "PROJECT": str(repo), "RUNTIME": str(runtime)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (runtime / "agent_owner_Phase-Curie.json").exists()
    assert not (runtime / "agent_owner_PhaseCurie.json").exists()
    assert not (runtime / "agent_owner_RequestedTuring.json").exists()
    assert list(runtime.glob("*.pending")) == []


def test_claude_launcher_does_not_turn_a_server_refusal_into_fallback(
    tmp_path: Path,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    env = _base_env(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls = tmp_path / "launcher-calls"
    for name, body in {
        "claude": "#!/bin/sh\nprintf 'claude\\n' >> \"$LAUNCHER_CALLS\"\n",
        "tmux": "#!/bin/sh\nprintf 'tmux:%s\\n' \"$*\" >> \"$LAUNCHER_CALLS\"\n",
        "pbcopy": "#!/bin/sh\ncat >/dev/null\n",
    }.items():
        path = fake_bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env python3
import json
import sys

request = json.load(sys.stdin)
tool = request["params"]["name"]
if tool == "register_agent":
    result = {"isError": True, "content": [{"type": "text", "text": "refused"}]}
else:
    result = {"structuredContent": {}}
print(json.dumps({"jsonrpc": "2.0", "id": "1", "result": result}))
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    launcher = ROOT / "bin" / "agent-start"
    result = subprocess.run(
        [str(launcher), "--project-key", str(repo), str(repo)],
        cwd=ROOT,
        env={
            **env,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', os.defpath)}",
            "AGENTSTACK_CLAUDE_BIN": str(fake_bin / "claude"),
            "AGENTSTACK_MCP_URL": "http://fake.invalid/mcp",
            "AGENTSTACK_MANAGED_AGENTS_FILE": "",
            "AGENTSTACK_HOOKS_DIR": "",
            "LAUNCHER_CALLS": str(calls),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "registration refused" in result.stderr
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "claude" not in recorded
    assert "new-session" not in recorded, "tmux launch started after a definitive refusal"
    runtime = Path(env["AGENTSTACK_RUNTIME_DIR"])
    assert not list(runtime.glob("agent_owner_*.json"))
    assert not list(runtime.glob("agent_token_*"))
    assert not list(runtime.glob("*.pending"))
