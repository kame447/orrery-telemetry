"""Project-isolation regressions for Dashboard control-plane mutations."""
from __future__ import annotations

import os
import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from dashboard import server


def _forbidden(*_args, **_kwargs):
    raise AssertionError("foreign or unvalidated control side effect")


def _launcher(path: pathlib.Path) -> pathlib.Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _target(name: str, *, category: str = "finished", running: bool = False) -> dict:
    return {
        "name": name,
        "category": category,
        "running": running,
        "attached": False,
    }


def test_jump_refuses_foreign_same_name_live_session_before_terminal_mutation(monkeypatch):
    monkeypatch.setattr(server, "_agent_program", lambda _name: "claude-code")
    monkeypatch.setattr(server, "_codex_app_runtimes", lambda: {})
    monkeypatch.setattr(server, "_terminal_adapter", lambda: "ghostty")
    monkeypatch.setattr(server, "_has_session", lambda _name: True)
    monkeypatch.setattr(server, "_live_session_matches_dashboard_project", lambda _name: False)
    monkeypatch.setattr(server, "build_agents", _forbidden)
    monkeypatch.setattr(server, "_focus_existing_terminal", _forbidden)
    monkeypatch.setattr(server, "_open_terminal_tmux", _forbidden)

    result = server.do_jump("SharedAgent")

    assert result == {"ok": False, "error": "tmux session belongs to another project"}


def test_exit_refuses_foreign_same_name_live_session_before_signal(monkeypatch):
    monkeypatch.setattr(server, "build_agents", lambda _days: [_target("SharedAgent", category="agent", running=True)])
    monkeypatch.setattr(server, "_has_session", lambda _name: True)
    monkeypatch.setattr(server, "_live_session_matches_dashboard_project", lambda _name: False)
    monkeypatch.setattr(server, "_mcp_call", _forbidden)
    monkeypatch.setattr(server.subprocess, "run", _forbidden)

    result = server.do_exit("SharedAgent")

    assert result == {"ok": False, "error": "tmux session belongs to another project"}


def test_kill_refuses_foreign_same_name_live_session_before_retire_or_tmux(monkeypatch):
    monkeypatch.setattr(server, "build_agents", lambda _days: [_target("SharedAgent")])
    monkeypatch.setattr(server, "_has_session", lambda _name: True)
    monkeypatch.setattr(server, "_live_session_matches_dashboard_project", lambda _name: False)
    monkeypatch.setattr(server, "_mcp_call", _forbidden)
    monkeypatch.setattr(server.subprocess, "run", _forbidden)

    result = server.do_kill("SharedAgent", "both")

    assert result == {"ok": False, "error": "tmux session belongs to another project"}


def test_reactivate_refuses_foreign_same_name_live_session_before_mail_mutation(monkeypatch):
    monkeypatch.setattr(server, "_has_retired_at", lambda: True)
    monkeypatch.setattr(server, "_project_key", lambda: "/project/A")
    monkeypatch.setattr(server, "_has_session", lambda _name: True)
    monkeypatch.setattr(server, "_live_session_matches_dashboard_project", lambda _name: False)
    monkeypatch.setattr(server, "_db", _forbidden)
    monkeypatch.setattr(server, "_mcp_call", _forbidden)

    result = server.do_reactivate("SharedAgent")

    assert result == {"ok": False, "error": "tmux session belongs to another project"}


def test_resume_refuses_foreign_same_name_live_session_before_transcript_lookup(monkeypatch):
    monkeypatch.setattr(server, "_live_session_conflicts_with_dashboard", lambda _name: True)
    monkeypatch.setattr(server, "_agent_program", _forbidden)
    monkeypatch.setattr(server, "_transcript_path", _forbidden)

    result = server.do_resume("SharedAgent")

    assert result == {"ok": False, "error": "tmux session belongs to another project"}


def test_resume_launches_with_fresh_full_project_context(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    transcript = tmp_path / "12345678.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    claude = _launcher(tmp_path / "claude")
    captured = {}
    context_args = [
        "-e", "AGENTSTACK_PROJECT_KEY=/project/A",
        "-e", "PROJECT_KEY=/project/A",
        "-e", "AGENTSTACK_PROJECT_CONTEXT_JSON={}",
    ]

    monkeypatch.setattr(server, "ABS_CLAUDE", str(claude))
    monkeypatch.setattr(server, "_live_session_conflicts_with_dashboard", lambda _name: False)
    monkeypatch.setattr(server, "_agent_program", lambda _name: "claude-code")
    monkeypatch.setattr(server, "_codex_app_runtimes", lambda: {})
    monkeypatch.setattr(server, "_transcript_path", lambda _name: str(transcript))
    monkeypatch.setattr(server, "_transcript_cwd", lambda _path: str(work))
    monkeypatch.setattr(server, "_project_key", lambda: "/project/A")
    monkeypatch.setattr(server, "_tmux_project_context_args", lambda cwd, key: (context_args, ""))
    monkeypatch.setattr(server, "_login_shell", lambda: "/bin/bash")

    def open_tmux(args, title):
        captured["args"] = list(args)
        captured["title"] = title
        return {"ok": True, "adapter": "test"}

    monkeypatch.setattr(server, "_open_terminal_tmux", open_tmux)

    result = server.do_resume("SharedAgent")

    assert result["ok"] is True
    args = captured["args"]
    assert args[:2] == ["tmux", "new-session"]
    assert ["-A", "-s", "SharedAgent", "-c", str(work)] == args[2 + len(context_args):7 + len(context_args)]
    for index in range(0, len(context_args), 2):
        assert context_args[index:index + 2] in [args[i:i + 2] for i in range(len(args) - 1)]
    inner = args[-1]
    assert "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR" in inner
    assert captured["title"] == "SharedAgent"


def test_delegated_cross_repository_spawn_fails_before_registration(tmp_path, monkeypatch):
    work = tmp_path / "foreign"
    work.mkdir()
    launch = _launcher(tmp_path / "spawn.sh")
    monkeypatch.setattr(server, "PREREGISTER_CHILD", __file__)
    monkeypatch.setattr(server, "_project_key", lambda: "/project/A")

    def context(path, project_key="", require_dashboard=False):
        assert path == str(work)
        assert project_key == "/project/A"
        assert require_dashboard is True
        return None, None, "work directory belongs to another project/repository"

    monkeypatch.setattr(server, "_runtime_context_for_work_dir", context)
    monkeypatch.setattr(server, "_preregister_dashboard_child", _forbidden)
    monkeypatch.setattr(server, "_mcp_call", _forbidden)
    spec = server.SpawnLaunchSpec(
        provider="claude", program="claude-code", model="claude-sonnet-5",
        script=str(launch),
    )

    result = server.spawn_with_launch_spec({
        "parent": "Parent", "name": "Sunny-Curie", "task": "work",
        "dir": str(work),
    }, spec)

    assert result == {
        "ok": False,
        "error": "delegated child work directory belongs to another project/repository",
    }


def test_delegated_spawn_uses_parent_preregistration_and_full_context(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    launch = _launcher(tmp_path / "spawn.sh")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    log_dir = tmp_path / "dashboard"
    log_dir.mkdir()
    prereg = {}
    popen = {}
    mail = []
    project_env = {
        "AGENTSTACK_PROJECT_KEY": "/project/A",
        "PROJECT_KEY": "/project/A",
        "AGENTSTACK_PROJECT_REPOSITORY": "/repo-id/A",
        "AGENTSTACK_PROJECT_WORK_DIR": str(work),
        "AGENTSTACK_PROJECT_WORKTREE_ROOT": str(work),
        "AGENTSTACK_PROTECTED_ROOTS": str(work),
        "AGENTSTACK_PROJECT_CONTEXT_JSON": '{"project_key":"/project/A"}',
        "AGENTSTACK_PROJECT_CONTEXT": "1",
    }
    context = {
        "project_key": "/project/A", "repository": "/repo-id/A",
        "work_dir": str(work), "launch_dir": str(work),
        "protected_roots": str(work),
    }

    monkeypatch.setattr(server, "PREREGISTER_CHILD", __file__)
    monkeypatch.setattr(server, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(server, "HERE", str(log_dir))
    monkeypatch.setattr(server, "_project_key", lambda: "/project/A")
    monkeypatch.setattr(server, "_runtime_context_for_work_dir", lambda *_a, **_k: (context, dict(project_env), ""))
    monkeypatch.setattr(server, "_project_has_agent", lambda project, name: (project, name) == ("/project/A", "Parent"))
    monkeypatch.setattr(server, "_runtime_agent_token", lambda name: "parent-token" if name == "Parent" else "")
    monkeypatch.setattr(server, "_runtime_agent_token_project", lambda name: "/project/A" if name == "Parent" else None)
    monkeypatch.setattr(server, "_spawn_name_status", lambda *_args: "available")

    def preregister(**kwargs):
        prereg.update(kwargs)
        token_path = pathlib.Path(kwargs["token_file"])
        token_path.write_text("child-token", encoding="utf-8")
        return {"ok": True, "child_name": "Sunny-Curie", "token": "child-token", "token_file": str(token_path)}

    def mcp(method, args, timeout=15):
        mail.append((method, dict(args), timeout))
        return {"ok": True, "data": {}}

    class Proc:
        pid = 12345
        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        popen["args"] = list(args)
        popen["kwargs"] = kwargs
        return Proc()

    def fake_run(args, *a, **k):
        if args[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess: {args}")

    monkeypatch.setattr(server, "_preregister_dashboard_child", preregister)
    monkeypatch.setattr(server, "_mcp_call", mcp)
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    monkeypatch.setenv("GIT_DIR", "/foreign/.git")
    spec = server.SpawnLaunchSpec(
        provider="claude", program="claude-code", model="claude-sonnet-5",
        script=str(launch),
    )

    result = server.spawn_with_launch_spec({
        "parent": "Parent", "name": "Sunny-Curie", "task": "work",
        "dir": str(work),
    }, spec)

    assert result["ok"] is True
    assert prereg["parent"] == "Parent"
    assert prereg["project_key"] == "/project/A"
    assert prereg["work_dir"] == str(work)
    assert [call[0] for call in mail] == ["send_message"]
    assert mail[0][1]["sender_token"] == "parent-token"
    env = popen["kwargs"]["env"]
    for key, value in project_env.items():
        assert env[key] == value
    assert "GIT_DIR" not in env
    assert env["PARENT_AGENT"] == "Parent"


def test_standalone_spawn_uses_target_repository_namespace(tmp_path, monkeypatch):
    work = tmp_path / "other-repo"
    work.mkdir()
    launch = _launcher(tmp_path / "spawn.sh")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    log_dir = tmp_path / "dashboard"
    log_dir.mkdir()
    prereg = {}
    popen = {}
    project_env = {
        "AGENTSTACK_PROJECT_KEY": "/project/B",
        "PROJECT_KEY": "/project/B",
        "AGENTSTACK_PROJECT_REPOSITORY": "/repo-id/B",
        "AGENTSTACK_PROJECT_WORK_DIR": str(work),
        "AGENTSTACK_PROJECT_WORKTREE_ROOT": str(work),
        "AGENTSTACK_PROTECTED_ROOTS": str(work),
        "AGENTSTACK_PROJECT_CONTEXT_JSON": '{"project_key":"/project/B"}',
        "AGENTSTACK_PROJECT_CONTEXT": "1",
    }
    context = {
        "project_key": "/project/B", "repository": "/repo-id/B",
        "work_dir": str(work), "launch_dir": str(work),
        "protected_roots": str(work),
    }

    monkeypatch.setattr(server, "PREREGISTER_CHILD", __file__)
    monkeypatch.setattr(server, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(server, "HERE", str(log_dir))
    monkeypatch.setattr(server, "_project_key", lambda: "/project/A")

    def context_for(path, project_key="", require_dashboard=False):
        assert path == str(work)
        assert project_key == ""
        assert require_dashboard is False
        return context, dict(project_env), ""

    monkeypatch.setattr(server, "_runtime_context_for_work_dir", context_for)
    monkeypatch.setattr(server, "_project_has_agent", _forbidden)
    monkeypatch.setattr(server, "_runtime_agent_token", _forbidden)
    monkeypatch.setattr(server, "_spawn_name_status", lambda name, project=None: "available" if project == "/project/B" else "unknown")

    def preregister(**kwargs):
        prereg.update(kwargs)
        token_path = pathlib.Path(kwargs["token_file"])
        token_path.write_text("child-token", encoding="utf-8")
        return {"ok": True, "child_name": "Sunny-Curie", "token": "child-token", "token_file": str(token_path)}

    class Proc:
        pid = 12346
        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        popen["kwargs"] = kwargs
        return Proc()

    monkeypatch.setattr(server, "_preregister_dashboard_child", preregister)
    monkeypatch.setattr(server, "_mcp_call", _forbidden)
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    spec = server.SpawnLaunchSpec(
        provider="claude", program="claude-code", model="claude-sonnet-5",
        script=str(launch), task_mail=False,
    )

    result = server.spawn_with_launch_spec({
        "standalone": True, "name": "Sunny-Curie", "task": "work",
        "dir": str(work),
    }, spec)

    assert result["ok"] is True
    assert prereg["project_key"] == "/project/B"
    assert prereg["parent"] == ""
    env = popen["kwargs"]["env"]
    assert env["AGENTSTACK_PROJECT_KEY"] == "/project/B"
    assert "PARENT_AGENT" not in env


def test_preregister_readback_rejects_foreign_durable_owner(tmp_path, monkeypatch):
    helper = _launcher(tmp_path / "agentstack-preregister-child")
    token_file = tmp_path / "handoff.token"
    monkeypatch.setattr(server, "PREREGISTER_CHILD", str(helper))

    def fake_run(args, **kwargs):
        pathlib.Path(args[args.index("--token-file-out") + 1]).write_text(
            "child-token", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="Sunny-Curie\n", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    monkeypatch.setattr(server, "_runtime_agent_token", lambda _name: "child-token")
    monkeypatch.setattr(server, "_runtime_agent_token_project", lambda _name: "/project/B")

    result = server._preregister_dashboard_child(
        project_key="/project/A", requested_name="Sunny-Curie", parent="Parent",
        program="claude-code", model="claude-sonnet-5", task_description="work",
        work_dir=str(tmp_path), token_file=str(token_file),
    )

    assert result["ok"] is False
    assert result["registration_retained"] is True
    assert "ownership could not be verified" in result["error"]


def test_preregister_helper_checks_parent_ownership_before_mail_side_effect(tmp_path):
    helper = pathlib.Path(__file__).resolve().parent.parent / "bin" / "agentstack-preregister-child"
    register_lib = tmp_path / "register.sh"
    trace = tmp_path / "trace"
    work = tmp_path / "work"
    work.mkdir()
    register_lib.write_text(
        'ags_apply_owned_workspace() { echo apply >> "$TRACE"; return 1; }\n'
        'ags_mcp_call() { echo mcp >> "$TRACE"; return 1; }\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "AGENTSTACK_REGISTER_LIB": str(register_lib),
        "AGENTSTACK_HOME": str(tmp_path / "home"),
        "TRACE": str(trace),
    })

    result = subprocess.run([
        "/bin/bash", str(helper), "--project-key", "/project/A",
        "--name", "Sunny-Curie", "--parent", "Parent",
        "--program", "claude-code", "--model", "claude-sonnet-5",
        "--task-description", "work", "--token-file-out", str(tmp_path / "token"),
        "--work-dir", str(work),
    ], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert trace.read_text(encoding="utf-8").splitlines() == ["apply"]
    assert "does not own the requested child workspace" in result.stderr
    assert not (tmp_path / "token").exists()


def test_preregister_helper_ensures_new_standalone_namespace_before_name_lookup(tmp_path):
    helper = pathlib.Path(__file__).resolve().parent.parent / "bin" / "agentstack-preregister-child"
    register_lib = tmp_path / "register.sh"
    trace = tmp_path / "trace"
    work = tmp_path / "work"
    work.mkdir()
    register_lib.write_text(
        'agentstack_resolve_invocation_context() { echo \'{"project_key":"/project/B"}\'; }\n'
        'agentstack_build_invocation_transport() { echo transport; }\n'
        'agentstack_validate_invocation_transport() { echo \'{"project_key":"/project/B"}\'; }\n'
        'ags_registration_context_project_key() { echo /project/B; }\n'
        'ags_mail_load_token() { echo mail >> "$TRACE"; }\n'
        'ags_mcp_call() { echo "mcp:$1" >> "$TRACE"; echo "{}"; return 0; }\n'
        'ags_mcp_has_error() { return 1; }\n'
        'ags_pick_available_agent_name() { echo pick >> "$TRACE"; return 1; }\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "AGENTSTACK_REGISTER_LIB": str(register_lib),
        "AGENTSTACK_HOME": str(tmp_path / "home"),
        "TRACE": str(trace),
    })

    result = subprocess.run([
        "/bin/bash", str(helper), "--project-key", "/project/B",
        "--program", "claude-code", "--model", "claude-sonnet-5",
        "--task-description", "work", "--token-file-out", str(tmp_path / "token"),
        "--work-dir", str(work),
    ], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "mail", "mcp:ensure_project", "pick",
    ]
    assert "could not pick an available child name" in result.stderr
