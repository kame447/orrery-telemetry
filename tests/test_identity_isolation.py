#!/usr/bin/env python3
"""Regression tests for tmux-scoped agent identity and owner-token isolation.

Runnable two ways (no third-party dependency required):
    python3 tests/test_identity_isolation.py
    pytest tests/test_identity_isolation.py
"""
from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def _isolated_env(root: pathlib.Path, overrides: dict[str, str] | None = None) -> dict[str, str]:
    home = root / "home"
    runtime = home / ".agentstack" / "runtime"
    temporary = root / "tmp"
    runtime.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(exist_ok=True)
    run_env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "TMUX_TMPDIR": str(temporary),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "AGENTSTACK_HOME": str(home / ".agentstack"),
        "AGENTSTACK_RUNTIME_DIR": str(runtime),
        "AGENTSTACK_MAIL_ENV": str(home / ".agentstack" / "mail" / "env.sh"),
        "AGENTSTACK_MANAGED_AGENTS_FILE": str(runtime / "managed-agents.txt"),
        "AGENTSTACK_MCP_URL": "http://127.0.0.1:1/mcp",
        "AGENTSTACK_LABEL_PREFIX": "org.agentstack.identity-test." + root.name,
        "AGENTSTACK_PYTHON": sys.executable,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    # Only the test's explicit synthetic overrides are inherited, never the
    # calling agent's tokens, BASH_ENV, installed paths, or service settings.
    if overrides:
        run_env.update(overrides)
    return run_env


def _run_bash(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="orrery-identity-shell-") as raw:
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", script],
            cwd=_ROOT,
            env=_isolated_env(pathlib.Path(raw), env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=20,
        )


def test_top_level_launchers_override_tmux_identity_environment():
    claude = _read("bin/agent-start")
    codex = _read("bin/agent-start-codex")

    assert '-e $(printf \'%q\' "AGENT_NAME=$SESSION_AGENT_NAME")' in claude
    for name in ("PARENT_AGENT", "CHILD_REGISTRATION_TOKEN"):
        assert f"-e {name}=" in claude, name
    for name in ("AGENT_NAME", "PARENT_AGENT", "CHILD_REGISTRATION_TOKEN"):
        assert f"-e {name}=" in codex, name
    assert "-e AGENTSTACK_RESERVED_IDENTITY=" in claude
    assert "-e AGENTSTACK_RESERVED_IDENTITY=" in codex

    # The owner token must be restored from its protected runtime file, not
    # expanded into the generated launcher or tmux command line.
    assert "SESSION_TOKEN_FILE" in claude
    assert 'export CHILD_REGISTRATION_TOKEN=$(printf' not in claude
    assert '-e "CHILD_REGISTRATION_TOKEN=$' not in claude


def test_bootstrap_ignores_unmarked_stale_identity_and_preserves_marked_reserved_identity():
    with tempfile.TemporaryDirectory(prefix="orrery-bootstrap-test-") as raw:
        root = pathlib.Path(raw)
        library = root / "bin" / "lib"
        library.mkdir(parents=True)
        bootstrap = root / "bin" / "agentstack-codex-bootstrap"
        # Execute the real bootstrap's identity logic, but never its sibling
        # production registration library or a user's installed env.sh.
        bootstrap.write_text(_read("bin/agentstack-codex-bootstrap"), encoding="utf-8")
        (root / "env.sh").write_text("export AGENTSTACK_PROJECT_KEY=\n", encoding="utf-8")
        guard = root / "unexpected-external-call"
        (library / "agentstack-register.sh").write_text(
            "ags_pick_adjective_scientist_name() { printf '%s\\n' Fresh-Dirac; }\n"
            "ags_test_forbidden() { printf '%s\\n' called >> \"$EXTERNAL_CALL_GUARD\"; return 97; }\n"
            "ags_mail_load_token() { ags_test_forbidden; }\n"
            "ags_mcp_call() { ags_test_forbidden; }\n"
            "ags_start_mail_watcher() { ags_test_forbidden; }\n"
            "ags_register_session() { ags_test_forbidden; }\n"
            "ags_record_managed_agent() { ags_test_forbidden; }\n",
            encoding="utf-8",
        )
        common = {
            "AGENTSTACK_PROJECT_KEY": "",
            "AGENTSTACK_MANAGED_AGENTS_FILE": "",
            "AGENTSTACK_RESERVED_IDENTITY": "",
            "AGENT_NAME": "Stale-Dirac",
            "PARENT_AGENT": "Stale-Parent",
            "CHILD_REGISTRATION_TOKEN": "stale-owner-token",
            "TMUX": "",
            "EXTERNAL_CALL_GUARD": str(guard),
        }
        # Compare classifications, not token values. Unexpected credentials
        # must not appear in stdout or a failing assertion.
        command = (
            f'source {shlex.quote(str(bootstrap))} {shlex.quote(str(root))} >/dev/null 2>&1 || exit $?; '
            'name=empty; parent=unexpected; token=unexpected; '
            'if [[ "${AGENT_NAME:-}" == Stale-Dirac ]]; then name=preserved; '
            'elif [[ -n "${AGENT_NAME:-}" ]]; then name=replaced; fi; '
            'if [[ -z "${PARENT_AGENT:-}" ]]; then parent=cleared; '
            'elif [[ "$PARENT_AGENT" == Stale-Parent ]]; then parent=preserved; fi; '
            'if [[ -z "${CHILD_REGISTRATION_TOKEN:-}" ]]; then token=cleared; '
            'elif [[ "$CHILD_REGISTRATION_TOKEN" == stale-owner-token ]]; then token=preserved; fi; '
            "printf '%s|%s|%s\\n' \"$name\" \"$parent\" \"$token\""
        )
        top_level = _run_bash(command, common).stdout.strip()
        assert top_level == "replaced|cleared|cleared", top_level
        reserved = _run_bash(command, {**common, "AGENTSTACK_RESERVED_IDENTITY": "1"}).stdout.strip()
        assert reserved == "preserved|preserved|preserved", reserved
        assert not guard.exists(), "identity-only test attempted an external operation"


def test_candidate_registration_rejects_ambient_owner_token():
    register_lib = _ROOT / "bin" / "lib" / "agentstack-register.sh"
    with tempfile.TemporaryDirectory() as tmp:
        capture = pathlib.Path(tmp) / "register-args"
        script = f'''
source "{register_lib}"
ags_mcp_call() {{
  local tool="$1"; shift
  if [[ "$tool" == "register_agent" ]]; then
    printf '%s\\n' "$@" > "$CAPTURE"
    printf '%s\\n' '{{"result":{{"structuredContent":{{"name":"Fresh-Dirac","registration_token":"server-token"}}}}}}'
  else
    printf '%s\\n' '{{"result":{{"structuredContent":{{}}}}}}'
  fi
}}
ags_agent_exists() {{ return 1; }}
ags_generate_registration_token() {{ printf '%s\\n' fresh-owner-token; }}
ags_store_registration_token() {{ return 0; }}
ags_apply_contact_policy() {{ return 0; }}
CHILD_REGISTRATION_TOKEN=stale-owner-token
export CHILD_REGISTRATION_TOKEN CAPTURE
ags_register_session /project codex model cx /work Fresh-Dirac candidate >/dev/null
'''
        _run_bash(script, {"CAPTURE": str(capture)})
        args = capture.read_text(encoding="utf-8")
        assert "registration_token=fresh-owner-token" in args, args
        assert "stale-owner-token" not in args, args


def test_registration_adopts_the_server_returned_name_on_every_call():
    """Local ORRERY Mail removes hyphens, so response name is the identity."""
    register_lib = _ROOT / "bin" / "lib" / "agentstack-register.sh"
    script = f'''
source "{register_lib}"
ags_mcp_call() {{
  local tool="$1"; shift
  if [[ "$tool" == "register_agent" ]]; then
    printf '%s\\n' '{{"result":{{"structuredContent":{{"name":"FrostyPasteur","registration_token":"stable-owner-token"}}}}}}'
  else
    printf '%s\\n' '{{"result":{{"structuredContent":{{}}}}}}'
  fi
}}
ags_generate_registration_token() {{ printf '%s\\n' requested-owner-token; }}
ags_store_registration_token() {{ printf '%s|%s\\n' "$1" "$2"; }}
ags_apply_contact_policy() {{ :; }}
for _ in 1 2; do
  ags_register_session /project codex model cx /work Frosty-Pasteur candidate >/dev/null
  printf 'registered=%s token=%s substituted=%s requested=%s returned=%s\\n' \
    "$AGS_REGISTERED_AGENT_NAME" "$AGS_REGISTERED_REGISTRATION_TOKEN" \
    "$AGS_AGENT_NAME_SUBSTITUTED" "$AGS_REQUESTED_AGENT_NAME" \
    "$AGS_SERVER_RETURNED_AGENT_NAME"
done
'''
    result = _run_bash(script)
    assert result.stdout.splitlines() == [
        "registered=FrostyPasteur token=stable-owner-token substituted=1 "
        "requested=Frosty-Pasteur returned=FrostyPasteur",
        "registered=FrostyPasteur token=stable-owner-token substituted=1 "
        "requested=Frosty-Pasteur returned=FrostyPasteur",
    ]


def test_reserved_identity_refuses_a_server_substitution():
    """A child/resume already has inbox and tmux state under its requested name."""
    register_lib = _ROOT / "bin" / "lib" / "agentstack-register.sh"
    script = f'''
source "{register_lib}"
ags_mcp_call() {{
  if [[ "$1" == "register_agent" ]]; then
    printf '%s\\n' '{{"result":{{"structuredContent":{{"name":"OtherAgent","registration_token":"other-token"}}}}}}'
  else
    printf '%s\\n' '{{"result":{{"structuredContent":{{}}}}}}'
  fi
}}
CHILD_REGISTRATION_TOKEN=reserved-owner-token
export CHILD_REGISTRATION_TOKEN
set +e
ags_register_session /project codex model cx /work Reserved-Curie reserved >/dev/null
status=$?
printf 'status=%s registered=%s substituted=%s requested=%s returned=%s\\n' \
  "$status" "$AGS_REGISTERED_AGENT_NAME" "$AGS_AGENT_NAME_SUBSTITUTED" \
  "$AGS_REQUESTED_AGENT_NAME" "$AGS_SERVER_RETURNED_AGENT_NAME"
'''
    result = _run_bash(script)
    assert result.stdout.strip() == (
        "status=2 registered= substituted=1 requested=Reserved-Curie "
        "returned=OtherAgent"
    )


def _run_root_claude_substitution(*, collision: bool):
    temp = tempfile.TemporaryDirectory()
    tmpdir = pathlib.Path(temp.name)
    bindir = tmpdir / "bin"
    libdir = bindir / "lib"
    libdir.mkdir(parents=True)
    launcher = bindir / "agent-start"
    launcher.write_text(_read("bin/agent-start"), encoding="utf-8")
    launcher.chmod(0o755)
    tmux_log = tmpdir / "tmux.log"
    tmux_state = tmpdir / "tmux.state"
    fake_tmux = tmpdir / "tmux"
    fake_tmux.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{tmux_log}"\n'
        'case "$1" in\n'
        f'  display-message) [[ -f "{tmux_state}" ]] && cat "{tmux_state}" || echo RootBefore ;;\n'
        f'  has-session) exit {0 if collision else 1} ;;\n'
        f'  rename-session) printf "%s\\n" "$2" > "{tmux_state}" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    fake_claude = tmpdir / "claude"
    fake_claude.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    fake_claude.chmod(0o755)
    (libdir / "agentstack-launch.sh").write_text(
        'ags_die() { printf "%s: %s\\n" "$AGS_PROG" "$*" >&2; exit 1; }\n'
        "ags_load_env() { :; }\n"
        'ags_resolve_tmux() { printf "%s\\n" "$FAKE_TMUX"; }\n'
        'ags_choose_dir() { printf "%s\\n" "$1"; }\n',
        encoding="utf-8",
    )
    (libdir / "agentstack-register.sh").write_text(
        'ags_pick_adjective_scientist_name() { printf "Zesty-Einstein\\n"; }\n'
        "ags_mail_load_token() { :; }\n"
        "ags_mcp_call() { :; }\n"
        "ags_start_mail_watcher() { :; }\n"
        "ags_register_session() {\n"
        '  AGS_REGISTERED_AGENT_NAME="MossyEagle"\n'
        '  AGS_REQUESTED_AGENT_NAME="Zesty-Einstein"\n'
        "  AGS_AGENT_NAME_SUBSTITUTED=1\n"
        "  return 0\n"
        "}\n"
        "ags_record_managed_agent() { :; }\n",
        encoding="utf-8",
    )
    env = _isolated_env(tmpdir, {
        "TMUX": "/tmp/fake,1,0",
        "FAKE_TMUX": str(fake_tmux),
        "AGENTSTACK_CLAUDE_BIN": str(fake_claude),
        "AGENTSTACK_PROJECT_KEY": "/project",
        "AGENTSTACK_MANAGED_AGENTS_FILE": str(tmpdir / "managed"),
        "AGENTSTACK_HOOKS_DIR": str(tmpdir),
    })
    result = subprocess.run(
        [str(launcher), str(tmpdir)],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=20,
    )
    calls = tmux_log.read_text(encoding="utf-8") if tmux_log.exists() else ""
    temp.cleanup()
    return result, calls


def test_root_claude_renames_tmux_to_the_server_returned_identity():
    result, calls = _run_root_claude_substitution(collision=False)
    assert result.returncode == 0, result.stderr
    assert "changed requested identity 'Zesty-Einstein' to 'MossyEagle'" in result.stderr
    assert "rename-session MossyEagle" in calls


def test_root_claude_stops_when_the_returned_tmux_name_is_occupied():
    result, calls = _run_root_claude_substitution(collision=True)
    assert result.returncode != 0
    assert "tmux session already exists" in result.stderr
    assert "rename-session" not in calls


def test_reserved_child_marker_and_rename_failure_are_explicit():
    spawn = _read("hooks/spawn_child.sh")
    bootstrap = _read("bin/agentstack-codex-bootstrap")
    launcher = _read("bin/agent-start-codex")
    assert spawn.count('-e "AGENTSTACK_RESERVED_IDENTITY=1"') >= 2
    assert 'rename-session "$AGENT_NAME" 2>/dev/null || true' not in bootstrap
    assert 'rename-session "$AGENT_NAME" 2>/dev/null || true' not in _read("bin/agent-start")
    assert "refusing an identity split" in _read("bin/agent-start")
    assert "refusing identity registration" in bootstrap
    assert "changed reserved identity" in bootstrap
    assert "agent registration skipped" in bootstrap
    assert "tmux rename-session failed: current session" in bootstrap
    assert '"$TMUX_IDENTITY_MATCHED" == "1"' in bootstrap
    assert 'source $(printf \'%q\' "$BOOTSTRAP") $(printf \'%q\' "$DIR") && $CODEX_CMD' in launcher


def test_doctor_and_hook_do_not_print_owner_token_value():
    doctor = _read("scripts/doctor.sh")
    reminder = _read("hooks/session-start-reminder.sh")
    assert "show-environment -g \"$identity_var\"" in doctor
    assert "STALE_IDENTITY_VARS" in doctor
    assert "agent_token_${SHELL_REGISTERED_AGENT}" in reminder
    assert "registration_token" in reminder


def test_bash_helper_does_not_inherit_home_credentials_or_startup_hooks():
    with tempfile.TemporaryDirectory(prefix="orrery-poisoned-parent-") as raw:
        outer = pathlib.Path(raw)
        marker = outer / "startup-ran"
        startup = outer / "startup.sh"
        startup.write_text(f'touch {shlex.quote(str(marker))}\n', encoding="utf-8")
        poisoned = {
            "HOME": str(outer), "BASH_ENV": str(startup), "ENV": str(startup),
            "HTTP_BEARER_TOKEN": "synthetic-parent-secret",
            "CHILD_REGISTRATION_TOKEN": "synthetic-parent-owner",
            "AGENTSTACK_RUNTIME_DIR": str(outer / "production-runtime"),
            "AGENTSTACK_MCP_URL": "http://127.0.0.1:18765/mcp",
        }
        with mock.patch.dict(os.environ, poisoned):
            result = _run_bash(
                'test -z "${BASH_ENV:-}${ENV:-}${HTTP_BEARER_TOKEN:-}${CHILD_REGISTRATION_TOKEN:-}" || exit 91; '
                "printf '%s\\n' \"$HOME\" \"$AGENTSTACK_RUNTIME_DIR\" \"$AGENTSTACK_MCP_URL\""
            )
        assert not marker.exists(), "inherited shell startup code executed"
        home, runtime, endpoint = result.stdout.splitlines()
        assert home != str(outer)
        assert pathlib.Path(runtime).is_relative_to(home)
        assert endpoint != poisoned["AGENTSTACK_MCP_URL"]
        assert not pathlib.Path(home).exists(), "isolated shell HOME was not cleaned up"


def test_bash_helper_cleans_temporary_home_on_failure():
    with tempfile.TemporaryDirectory(prefix="orrery-failed-shell-") as raw:
        capture = pathlib.Path(raw) / "home-path"
        try:
            _run_bash("printf '%s' \"$HOME\" > \"$CAPTURE\"; exit 9", {"CAPTURE": str(capture)})
        except subprocess.CalledProcessError as error:
            assert error.returncode == 9
        else:
            raise AssertionError("failed shell was reported as success")
        assert not pathlib.Path(capture.read_text(encoding="utf-8")).exists()


def test_bash_helper_cleans_temporary_home_on_timeout():
    captured: dict[str, str] = {}

    def timeout(command, **kwargs):
        captured.update(kwargs["env"])
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired(command, 20)

    with mock.patch.object(subprocess, "run", side_effect=timeout):
        try:
            _run_bash("printf should-not-run")
        except subprocess.TimeoutExpired:
            pass
        else:
            raise AssertionError("timed-out shell was reported as success")
    assert not pathlib.Path(captured["HOME"]).exists()


def test_bootstrap_identity_fixture_ignores_poisoned_parent_environment():
    with tempfile.TemporaryDirectory(prefix="orrery-bootstrap-parent-") as raw:
        root = pathlib.Path(raw)
        marker = root / "startup-ran"
        startup = root / "startup.sh"
        startup.write_text(f'touch {shlex.quote(str(marker))}\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {
            "HOME": str(root), "BASH_ENV": str(startup),
            "AGENTSTACK_PROJECT_KEY": "synthetic-unrelated-project",
            "AGENTSTACK_RESERVED_IDENTITY": "1",
            "CHILD_REGISTRATION_TOKEN": "synthetic-parent-owner",
            "AGENTSTACK_MAIL_ENV": str(root / "user-mail-env"),
        }):
            test_bootstrap_ignores_unmarked_stale_identity_and_preserves_marked_reserved_identity()
        assert not marker.exists(), "bootstrap fixture sourced a parent startup file"


def _main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except (AssertionError, subprocess.CalledProcessError) as error:
                failures += 1
                print(f"FAIL {name}: {error}")
    print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
