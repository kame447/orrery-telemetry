"""Child/resume launch boundaries derive protection from their actual target.

Only temporary repositories, homes and mocked Mail/tmux are used here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from dashboard import server
from test_claude_resume_mail import resume, child, NAME  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "hooks" / "project-context.sh"
FIELDS = ("PROJECT_KEY", "AGENTSTACK_PROJECT_KEY", "AGENTSTACK_PROJECT_REPOSITORY",
          "AGENTSTACK_PROJECT_WORK_DIR", "AGENTSTACK_PROJECT_WORKTREE_ROOT",
          "AGENTSTACK_PROTECTED_ROOTS", "AGENTSTACK_EXTRA_PROTECTED_ROOTS",
          "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")
DUMP = shlex.join([sys.executable, "-c", "import json,os; print(json.dumps({k:os.environ.get(k) for k in " + repr(FIELDS) + "}))"])


def git(*args):
    subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True)


def repository(path):
    path.mkdir()
    git("init", path)
    git("-C", path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-m", "fixture")
    return path


def environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return {"PATH": os.environ["PATH"], "HOME": str(home), "AGENTSTACK_HOME": str(home / ".agentstack"),
            "AGENTSTACK_LABEL_PREFIX": "org.agentstack.test.dynamic-child",
            "AGENTSTACK_PROTECTED_ROOTS": "/old/runtime/root", "GIT_DIR": "/old/.git",
            "GIT_WORK_TREE": "/old", "GIT_COMMON_DIR": "/old/.git"}


def test_child_target_replaces_parent_and_recomputes_worktree_before_forwarding(tmp_path):
    source = repository(tmp_path / "source")
    linked = tmp_path / "child-worktree"
    git("-C", source, "worktree", "add", "-b", "child", linked)
    vault = linked / "vault"
    vault.mkdir()
    text = (ROOT / "hooks" / "spawn_child.sh").read_text()
    functions = ""
    for name in ("prepare_child_workspace_context", "append_child_workspace_environment"):
        start = text.index(name + "() {")
        functions += text[start:text.index("\n}\n", start) + 3]
    command = (f"source {shlex.quote(str(CONTEXT))}; " + functions +
               f"PROJECT_KEY=shared-mail; WORK_DIR={shlex.quote(str(source))}; "
               "prepare_child_workspace_context || exit $?; "
               f"WORK_DIR={shlex.quote(str(vault))}; "
               "prepare_child_workspace_context || exit $?; "
               "TMUX_ENV_ARGS=(); append_child_workspace_environment; " + DUMP +
               "; printf '%s\\n' \"${TMUX_ENV_ARGS[@]}\"")
    env = environment(tmp_path)
    env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = str(vault)
    result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    context = json.loads(lines[0])
    assert context["PROJECT_KEY"] == "shared-mail"
    assert context["AGENTSTACK_PROJECT_REPOSITORY"] == str(source)
    assert context["AGENTSTACK_PROJECT_WORK_DIR"] == str(vault)
    assert context["AGENTSTACK_PROJECT_WORKTREE_ROOT"] == str(linked)
    assert context["AGENTSTACK_PROTECTED_ROOTS"] == f"{vault}:{linked}"
    assert all(context[key] is None for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"))
    assert f"AGENTSTACK_PROTECTED_ROOTS={vault}:{linked}" in lines
    assert f"AGENTSTACK_EXTRA_PROTECTED_ROOTS={vault}" in lines
    assert "/old/runtime/root" not in result.stdout


@pytest.mark.parametrize("extra", [None, "", "live"], ids=["installed", "explicit-empty", "live"])
def test_dashboard_resume_uses_saved_target_and_explicit_extras(tmp_path, monkeypatch, extra):
    target = repository(tmp_path / "resumed")
    env = environment(tmp_path)
    install = Path(env["AGENTSTACK_HOME"])
    install.mkdir()
    installed_vault = tmp_path / "installed vault"
    installed_vault.mkdir()
    live_vault = tmp_path / "live vault"
    live_vault.mkdir()
    (install / "env.sh").write_text("export AGENTSTACK_EXTRA_PROTECTED_ROOTS=" + shlex.quote(str(installed_vault)) + "\n")
    monkeypatch.setenv("AGENTSTACK_HOME", str(install))
    monkeypatch.delenv("AGENTSTACK_EXTRA_PROTECTED_ROOTS", raising=False)
    if extra is not None:
        monkeypatch.setenv("AGENTSTACK_EXTRA_PROTECTED_ROOTS", str(live_vault) if extra else "")
    # This is an old tmux server value, not an explicit dashboard setting.
    env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = "/stale/server/extra"
    prelude = server._resume_workspace_prelude(str(target), "logical-mail", str(ROOT / "hooks"))
    result = subprocess.run(["bash", "-c", prelude + DUMP], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)
    expected_extra = str(installed_vault) if extra is None else str(live_vault) if extra else ""
    assert context["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] == expected_extra
    assert context["AGENTSTACK_PROTECTED_ROOTS"] == ":".join(filter(None, [expected_extra, str(target)]))
    assert context["AGENTSTACK_PROJECT_KEY"] == "logical-mail"
    assert context["AGENTSTACK_PROJECT_WORK_DIR"] == str(target)
    assert all(context[key] is None for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"))


def test_resume_invalid_target_stops_before_provider_exec(tmp_path, monkeypatch):
    env = environment(tmp_path)
    monkeypatch.setenv("AGENTSTACK_HOME", env["AGENTSTACK_HOME"])
    prelude = server._resume_workspace_prelude(str(tmp_path / "missing"), "shared", str(ROOT / "hooks"))
    result = subprocess.run(["bash", "-c", prelude + "printf PROVIDER_STARTED"], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "PROVIDER_STARTED" not in result.stdout


@pytest.mark.parametrize("provider", ["codex", "gemini"])
@pytest.mark.parametrize("custom_hooks", [False, True], ids=["owned-hooks", "custom-hooks"])
def test_bootstrap_recomputes_roots_even_when_mail_is_down(tmp_path, provider, custom_hooks):
    target = repository(tmp_path / "actual")
    env = environment(tmp_path)
    install = Path(env["AGENTSTACK_HOME"])
    (install / "hooks").mkdir(parents=True)
    (install / "bin" / "lib").mkdir(parents=True)
    shutil.copy2(CONTEXT, install / "hooks")
    bootstrap = install / "bin" / f"agentstack-{provider}-bootstrap"
    shutil.copy2(ROOT / "bin" / bootstrap.name, bootstrap)
    (install / "bin" / "lib" / "agentstack-register.sh").write_text(
        "ags_pick_adjective_scientist_name() { printf FixtureCurie; }\n"
        "ags_mail_load_token() { :; }\nags_mcp_call() { return 1; }\n")
    env["AGENTSTACK_PROJECT_KEY"] = "shared"
    env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = ""
    if custom_hooks:
        unrelated_hooks = tmp_path / "custom-hooks"
        unrelated_hooks.mkdir()
        env["AGENTSTACK_HOOKS_DIR"] = str(unrelated_hooks)
    result = subprocess.run(["bash", "-c", f"source {shlex.quote(str(bootstrap))} {shlex.quote(str(target))} || exit $?; " + DUMP],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)
    assert context["AGENTSTACK_PROTECTED_ROOTS"] == str(target)
    assert context["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] == ""
    assert context["AGENTSTACK_PROJECT_KEY"] == "shared"
    assert all(context[key] is None for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"))


@pytest.mark.parametrize("codex", [False, True], ids=["claude", "codex"])
def test_child_provider_receives_pinned_target_after_login_defaults(tmp_path, codex):
    from test_claude_child_chrome import _launch_env, _spawn, _new_session, _codex_inner

    env, _ = _launch_env(tmp_path, codex=codex)
    # A supported hooks override may customize helpers without duplicating
    # launch policy. The resolver must come from spawn_child.sh's own version.
    custom_hooks = tmp_path / "custom-hooks"
    custom_hooks.mkdir()
    for entry in (ROOT / "hooks").iterdir():
        if entry.name != "project-context.sh":
            (custom_hooks / entry.name).symlink_to(entry)
    env["AGENTSTACK_HOOKS_DIR"] = str(custom_hooks)
    target = repository(tmp_path / "approved")
    nested = target / "nested"
    nested.mkdir()
    vault = target / "shared vault"
    vault.mkdir()
    env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = str(vault)
    env["AGENTSTACK_PROTECTED_ROOTS"] = "/previous/agent/root"
    result = _spawn(tmp_path, env, nested, "WorkspaceChild", codex=codex)
    assert result.returncode == 0, result.stderr
    session = _new_session(env)
    tmux_env = dict(session[i + 1].split("=", 1) for i, arg in enumerate(session[:-1]) if arg == "-e")
    assert tmux_env["AGENTSTACK_LAUNCH_CONTEXT_HELPER"] == str(CONTEXT)
    inner = _codex_inner(env) if codex else shlex.split(session[-1])[-1]
    dump = tmp_path / "provider-env.json"
    cli = Path(env["HOME"]) / ".local" / "bin" / ("codex" if codex else "claude")
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("#!/bin/bash\n" + shlex.join([sys.executable, "-c",
        "import json,os,pathlib; pathlib.Path(" + repr(str(dump)) + ").write_text(json.dumps({'cwd':os.getcwd(),'env':dict(os.environ)}))"]) + "\n")
    cli.chmod(0o700)
    hooks = tmp_path / "noop-hooks"
    hooks.mkdir()
    (hooks / "cleanup-child-agent.sh").write_text("exit 0\n")
    # Simulate a login profile changing cwd and reloading another install's
    # namespace/protection. The child-scoped snapshots remain authoritative.
    run_env = {**env, **tmux_env, "AGENTSTACK_CODEX_BIN": str(cli), "AGENTSTACK_HOOKS_DIR": str(hooks),
               "PROJECT_KEY": "stale-login", "AGENTSTACK_PROJECT_KEY": "stale-login",
               "AGENTSTACK_EXTRA_PROTECTED_ROOTS": "/stale/login/extra",
               "AGENTSTACK_PROTECTED_ROOTS": "/stale/login/root", "GIT_DIR": "/stale/.git"}
    result = subprocess.run(["bash", "-c", inner], env=run_env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    seen = json.loads(dump.read_text())
    assert seen["cwd"] == str(nested)
    assert seen["env"]["PROJECT_KEY"] == "/shared/project"
    assert seen["env"]["AGENTSTACK_PROTECTED_ROOTS"] == f"{vault}:{target}"
    assert seen["env"]["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] == str(vault)
    assert seen["env"]["AGENTSTACK_PROTECTION_CONTEXT"] == "workspace-v1"
    assert "GIT_DIR" not in seen["env"]
    assert "AGENTSTACK_LAUNCH_WORK_DIR" not in seen["env"]


@pytest.mark.parametrize("invalid", ["broken-git", "extra-control", "missing-helper"])
def test_invalid_claude_resume_context_leaves_retained_identity_unchanged(resume, tmp_path, monkeypatch, invalid):
    runtime, registration, launches, calls = resume
    child(runtime, registration)
    state_path = runtime / "child-agents" / f"{NAME}.json"
    before = state_path.read_bytes()
    if invalid == "broken-git":
        (tmp_path / ".git").write_text("gitdir: /missing/worktree/metadata\n")
    elif invalid == "extra-control":
        monkeypatch.setenv("AGENTSTACK_EXTRA_PROTECTED_ROOTS", "/vault\ninvalid")
    else:
        monkeypatch.setattr(server, "HOOKS_DIR", str(tmp_path / "missing-hooks"))
    result = server.do_resume(NAME, open_terminal=False, replace_husk=True)
    assert result["ok"] is False
    assert result["resume_capability"] == "config_unrestorable"
    assert not launches and not calls
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("provider", ["codex", "gemini"])
def test_bootstrap_missing_workspace_helper_fails_before_registration(tmp_path, provider):
    target = tmp_path / "workspace"
    target.mkdir()
    env = environment(tmp_path)
    install = Path(env["AGENTSTACK_HOME"])
    (install / "bin" / "lib").mkdir(parents=True)
    bootstrap = install / "bin" / f"agentstack-{provider}-bootstrap"
    shutil.copy2(ROOT / "bin" / bootstrap.name, bootstrap)
    marker = tmp_path / "register-loaded"
    (install / "bin" / "lib" / "agentstack-register.sh").write_text(
        "touch " + shlex.quote(str(marker)) + "\n")
    env["AGENTSTACK_PROJECT_KEY"] = "shared"
    env["AGENTSTACK_HOOKS_DIR"] = str(tmp_path / "missing-override")
    result = subprocess.run(["bash", "-c", f"source {shlex.quote(str(bootstrap))} {shlex.quote(str(target))} && printf PROVIDER_STARTED"],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "PROVIDER_STARTED" not in result.stdout
    assert not marker.exists()
