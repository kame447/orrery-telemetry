"""Protection must not change upstream bootstrap namespace or resume policy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS = (
    "AGENTSTACK_PROJECT_KEY", "PROJECT_KEY", "AGENTSTACK_PROJECT_WORK_DIR",
    "AGENTSTACK_PROJECT_WORKTREE_ROOT", "AGENTSTACK_PROTECTED_ROOTS",
    "AGENTSTACK_EXTRA_PROTECTED_ROOTS", "AGENTSTACK_PROTECTION_CONTEXT",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
)


def fixture(tmp_path: Path, provider: str) -> tuple[Path, Path, dict[str, str]]:
    install = tmp_path / "install"
    (install / "bin/lib").mkdir(parents=True)
    (install / "hooks").mkdir()
    for name in ("project-context.sh", "installed-env.py"):
        shutil.copy2(ROOT / "hooks" / name, install / "hooks" / name)
    bootstrap = install / "bin" / f"agentstack-{provider}-bootstrap"
    shutil.copy2(ROOT / "bin" / bootstrap.name, bootstrap)
    (install / "bin/lib/agentstack-register.sh").write_text("""
ags_pick_adjective_scientist_name() { printf FixtureAgent; }
ags_mail_load_token() { :; }
ags_mcp_call() { return 1; }
ags_record_managed_agent() { :; }
""")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(tmp_path),
           "AGENTSTACK_HOME": str(install), "AGENTSTACK_PYTHON": sys.executable,
           "AGENTSTACK_LABEL_PREFIX": "org.agentstack.bootstrap-context-test",
           "AGENTSTACK_RUNTIME_DIR": str(tmp_path / "runtime"), "TMUX": "",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    return bootstrap, workspace, env


def run_bootstrap(bootstrap: Path, workspace: Path, env: dict[str, str]) -> tuple[dict, str]:
    snapshot = "import json,os,sys; print(json.dumps({'status':int(sys.argv[1]), 'env':{k:os.environ.get(k) for k in " + repr(KEYS) + "}}))"
    command = '. "$1" "$2" >&2; result=$?; "$AGENTSTACK_PYTHON" -c "$3" "$result"'
    result = subprocess.run(["/bin/bash", "-c", command, "test", str(bootstrap),
                             str(workspace), snapshot], env=env, cwd=workspace,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result.stderr


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_bootstrap_recomputes_protection_and_keeps_selected_namespace(tmp_path: Path, provider: str) -> None:
    bootstrap, workspace, env = fixture(tmp_path, provider)
    shared = tmp_path / "shared"
    shared.mkdir()
    env.update({"AGENTSTACK_PROJECT_KEY": "opaque-shared-namespace",
                "AGENTSTACK_PROTECTED_ROOTS": "/stale/worktree",
                "AGENTSTACK_EXTRA_PROTECTED_ROOTS": str(shared),
                "GIT_DIR": "/stale/.git", "GIT_WORK_TREE": "/stale",
                "GIT_COMMON_DIR": "/stale/.git"})
    result, warning = run_bootstrap(bootstrap, workspace, env)
    assert result["status"] == 0, warning
    actual = result["env"]
    assert actual["AGENTSTACK_PROJECT_KEY"] == "opaque-shared-namespace"
    assert actual["PROJECT_KEY"] == "opaque-shared-namespace"
    assert actual["AGENTSTACK_PROTECTED_ROOTS"] == f"{shared}:{workspace}"
    assert actual["AGENTSTACK_PROTECTION_CONTEXT"] == "workspace-v1"
    assert all(actual[name] is None for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"))
    assert warning.count("legacy AGENTSTACK_PROTECTED_ROOTS ignored") == 1


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_bootstrap_empty_process_extras_override_installed_extras(tmp_path: Path, provider: str) -> None:
    bootstrap, workspace, env = fixture(tmp_path, provider)
    (bootstrap.parent.parent / "env.sh").write_text(
        "export AGENTSTACK_PROJECT_KEY=installed-namespace\n"
        "export AGENTSTACK_EXTRA_PROTECTED_ROOTS=/installed/shared\n"
        "export AGENTSTACK_PROTECTED_ROOTS=/installed/legacy\n")
    env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = ""
    result, warning = run_bootstrap(bootstrap, workspace, env)
    assert result["status"] == 0, warning
    assert result["env"]["AGENTSTACK_PROJECT_KEY"] == "installed-namespace"
    assert result["env"]["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] == ""
    assert result["env"]["AGENTSTACK_PROTECTED_ROOTS"] == str(workspace)


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_bootstrap_protection_does_not_invent_missing_namespace(tmp_path: Path, provider: str) -> None:
    bootstrap, workspace, env = fixture(tmp_path, provider)
    result, warning = run_bootstrap(bootstrap, workspace, env)
    assert result["status"] == 0, warning
    assert result["env"]["AGENTSTACK_PROJECT_KEY"] == ""
    assert result["env"]["PROJECT_KEY"] == ""
    assert result["env"]["AGENTSTACK_PROTECTED_ROOTS"] == str(workspace)


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_invalid_installed_roots_fail_before_env_shell_code(tmp_path: Path, provider: str) -> None:
    bootstrap, workspace, env = fixture(tmp_path, provider)
    marker = tmp_path / "must-not-run"
    (bootstrap.parent.parent / "env.sh").write_text(
        f'export AGENTSTACK_PROTECTED_ROOTS="$(touch {shlex.quote(str(marker))})"\n')
    result, warning = run_bootstrap(bootstrap, workspace, env)
    assert result["status"] != 0
    assert "invalid installed protection configuration" in warning
    assert not marker.exists()


def test_codex_resume_still_refuses_missing_registration_after_protection(tmp_path: Path) -> None:
    bootstrap, workspace, env = fixture(tmp_path, "codex")
    env.update({"AGENTSTACK_PROJECT_KEY": "namespace",
                "AGENTSTACK_CODEX_LAUNCH_KIND": "resume",
                "AGENTSTACK_CODEX_LAUNCH_ORIGIN": "standalone",
                "AGENTSTACK_CODEX_RESUME_SESSION_ID": "12345678-abcd-1234-abcd-123456789abc",
                "AGENTSTACK_PROTECTED_ROOTS": "/stale"})
    result, warning = run_bootstrap(bootstrap, workspace, env)
    assert result["status"] != 0
    assert result["env"]["AGENTSTACK_PROTECTED_ROOTS"] == str(workspace)
    assert "unreachable" in warning


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh unavailable")
def test_zsh_sourced_literal_reader_uses_helper_directory_after_cwd_change(tmp_path: Path) -> None:
    bootstrap, workspace, env = fixture(tmp_path, "codex")
    config = bootstrap.parent.parent / "env.sh"
    config.write_text("export AGENTSTACK_PROTECTED_ROOTS='/shared root'\n"
                      "export AGENTSTACK_EXTRA_PROTECTED_ROOTS=''\n")
    helper = bootstrap.parent.parent / "hooks/project-context.sh"
    script = ('. "$1"; cd "$2"; '
              'agentstack_installed_env_value AGENTSTACK_PROTECTED_ROOTS "$3"; '
              'printf "|"; '
              'agentstack_installed_env_value AGENTSTACK_EXTRA_PROTECTED_ROOTS "$3" present')
    result = subprocess.run([shutil.which("zsh"), "-eu", "-c", script, "test",
                             str(helper), str(workspace), str(config)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip("\n") == "/shared root|1"


def test_direct_hook_keeps_literal_vault_roots_beside_unrelated_shell_logic(tmp_path: Path) -> None:
    bootstrap, workspace, env = fixture(tmp_path, "codex")
    shared = tmp_path / "shared vault"
    shared.mkdir()
    marker = tmp_path / "must-not-source"
    config = bootstrap.parent.parent / "env.sh"
    config.write_text(
        f"export AGENTSTACK_PROJECT_KEY={shlex.quote(str(workspace))}\n"
        f"export AGENTSTACK_PROTECTED_ROOTS={shlex.quote(str(shared))}\n"
        f"if true; then touch {shlex.quote(str(marker))}; fi\n")
    script = ('. "$1"; reservation_resolve_tool_context "$2" || exit 9; '
              'printf "%s|%s" "$MATCHED_ROOT" "$REL_PATH"')
    direct = subprocess.run(["/bin/bash", "-eu", "-c", script, "test",
        str(ROOT / "hooks/reservation-common.sh"),
        json.dumps({"tool_input": {"file_path": str(shared / "note.md")}})],
        env=env, cwd=workspace, capture_output=True, text=True, timeout=10)
    assert direct.returncode == 0, direct.stderr
    assert direct.stdout == f"{shared}|note.md"
    assert not marker.exists()

    # Only the unmanaged lookup is permissive. Managed launch/source and
    # install/update migration still reject this ambiguous whole-file input.
    managed = subprocess.run(["/bin/bash", str(ROOT / "hooks/project-context.sh"),
        "workspace-context-json", str(workspace), "namespace"], env=env,
        capture_output=True, text=True, timeout=10)
    assert managed.returncode != 0
    assert "ambiguous shell syntax" in managed.stderr
    assert managed.stdout == ""
    migration = subprocess.run([sys.executable, str(ROOT / "hooks/installed-env.py"),
        "state", "AGENTSTACK_PROTECTED_ROOTS", str(config)], env=env,
        capture_output=True, text=True, timeout=10)
    assert migration.returncode != 0
    assert migration.stdout.strip() == "invalid"
    assert not marker.exists()
