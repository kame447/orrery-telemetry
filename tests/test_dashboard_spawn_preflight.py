"""The dashboard refuses a child target before registering it.

The child launchers validate the target with the core project validator and
prove the child token in the canonical namespace. The dashboard runs the same
validator first, so a request the launcher would refuse never leaves a
registered child behind, and canonical requests still reach a working child.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dashboard.server as server  # noqa: E402
from test_child_lifecycle_isolation import (  # noqa: E402,F401
    CHILD, TOKEN, FakeMail, gemini_home, private, run, tmux_calls, token_file, world,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
REAL_RUN, REAL_POPEN = subprocess.run, subprocess.Popen


def _is_preflight(args) -> bool:
    return len(args) > 3 and args[0] == "/bin/bash" and args[3] == "spawn-preflight"


@pytest.fixture
def dashboard(monkeypatch, world):
    """do_spawn against a recording Mail, with only the launcher process faked."""
    for name in ("AGENTSTACK_PROJECT_KEY", "PROJECT_KEY", "AGENTSTACK_PROJECT_REPOSITORY",
                 "AGENTSTACK_PROJECT_WORK_DIR", "AGENTSTACK_PROJECT_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    world["runtime"].mkdir(parents=True, exist_ok=True)
    private(world["runtime"] / "agent_token_Parent", "parent-owner-token")
    state = {"mail": FakeMail(), "calls": [], "launched": []}

    def mcp(method, args, timeout=15):
        state["calls"].append((method, dict(args)))
        reply = state["mail"].answer(method, args)
        if "error" in reply:
            return {"ok": False, "error": reply["error"]["message"]}
        return {"ok": True, "data": reply["result"].get("structuredContent", {})}

    def fake_run(args, *rest, **kwargs):
        if _is_preflight(args):
            return REAL_RUN(args, *rest, **kwargs)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_popen(args, *rest, **kwargs):
        if _is_preflight(args):
            return REAL_POPEN(args, *rest, **kwargs)
        state["launched"].append((list(args), dict(kwargs.get("env") or {})))
        return None

    monkeypatch.setattr(server, "HOOKS_DIR", str(ROOT / "hooks"))
    monkeypatch.setattr(server, "SPAWN_SCRIPT", str(ROOT / "hooks" / "spawn_child.sh"))
    monkeypatch.setattr(server, "RUNTIME_DIR", str(world["runtime"]))
    monkeypatch.setattr(server, "HERE", str(world["tmp"]))
    monkeypatch.setattr(server, "_spawn_name_status", lambda _name: "available")
    monkeypatch.setattr(server, "_mcp_call", mcp)
    monkeypatch.setattr(server.time, "sleep", lambda _seconds: None)

    def spawn(project_key, work_dir, **service_env):
        monkeypatch.setattr(server, "_project_key", lambda: str(project_key))
        for key, value in service_env.items():
            monkeypatch.setenv(key, str(value))
        # server.subprocess is the stdlib module: patch it only for this call.
        with monkeypatch.context() as process_patch:
            process_patch.setattr(server.subprocess, "run", fake_run)
            process_patch.setattr(server.subprocess, "Popen", fake_popen)
            return server.do_spawn({"parent": "Parent", "name": CHILD, "task": "work",
                                    "dir": str(work_dir), "provider": "claude",
                                    "model": "claude-sonnet-5"})

    state["spawn"] = spawn
    return state


def _registrations(state) -> list[dict]:
    return [args for method, args in state["calls"] if method == "register_agent"]


@pytest.mark.parametrize("case", [
    "foreign-dir", "symlink-alias-key", "dot-alias-key",
    "logical-key-without-service-tuple", "logical-key-bound-to-another-repository",
])
def test_refused_targets_register_nothing(dashboard, world, case):
    alpha, beta = world["alpha"], world["beta"]
    service_env = {}
    if case == "foreign-dir":
        key, work_dir = alpha, beta
    elif case == "symlink-alias-key":
        key = world["home"] / "alpha-alias"
        key.symlink_to(alpha, target_is_directory=True)
        work_dir = alpha
    elif case == "dot-alias-key":
        key, work_dir = f"{alpha}/.", alpha
    elif case == "logical-key-without-service-tuple":
        key, work_dir = "team-x", alpha
    else:
        key, work_dir = "team-x", alpha
        service_env = {"AGENTSTACK_PROJECT_KEY": "team-x",
                       "AGENTSTACK_PROJECT_REPOSITORY": beta}
    result = dashboard["spawn"](key, work_dir, **service_env)
    assert result["ok"] is False, result
    if "alias" in case:
        assert "is not canonical" in result["error"]
    else:
        assert "is not valid for dir" in result["error"]
    assert dashboard["calls"] == []
    assert dashboard["launched"] == []


def test_logical_key_with_the_service_tuple_registers_and_launches(dashboard, world):
    result = dashboard["spawn"]("team-x", world["linked"],
                                AGENTSTACK_PROJECT_KEY="team-x",
                                AGENTSTACK_PROJECT_REPOSITORY=world["alpha"])
    assert result["ok"] is True, result
    assert [args["project_key"] for args in _registrations(dashboard)] == ["team-x"]
    assert len(dashboard["launched"]) == 1


def test_canonical_claude_handoff_is_accepted_by_the_real_launcher(dashboard, world):
    mail = dashboard["mail"]
    with mail:
        result = dashboard["spawn"](world["alpha"], world["linked"])
        assert result["ok"] is True, result
        (args, launch_env), = dashboard["launched"]
        token_handoff = pathlib.Path(args[args.index("--child-token-file") + 1])
        launched = run(world, ["/bin/bash", *args], cwd=world["home"], mail=mail,
                       PROJECT_KEY=launch_env["PROJECT_KEY"],
                       PARENT_AGENT=launch_env["PARENT_AGENT"])
    assert launched.returncode == 0, launched.stderr
    assert launched.stdout.strip() == CHILD
    assert [r["tool"] for r in mail.requests] == ["whois"]
    assert mail.requests[0]["args"]["project_key"] == str(world["alpha"])
    assert "new-session" in tmux_calls(world)
    assert not token_handoff.exists(), "the successful launch consumed the handoff"
    assert token_file(world).is_file()


def test_canonical_gemini_token_only_handoff_is_accepted_by_the_real_adapter(world):
    home = gemini_home(world)
    one_shot = private(world["tmp"] / "dashboard-handoff" / "child-token", TOKEN)
    task = private(world["tmp"] / "task.md", "task")
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", home / "hooks" / "spawn_gemini_preregistered.sh",
                             "--pre-registered", CHILD, "--child-token-file", one_shot,
                             "--model", "gemini-3.8-flash-high", "--worktree",
                             "task", world["alpha"]],
                     cwd=world["home"], mail=mail,
                     AGENTSTACK_HOME=home, AGENTSTACK_HOOKS_DIR=home / "hooks",
                     AGENTSTACK_REGISTER_LIB=None,
                     PROJECT_KEY=world["alpha"], AGENTSTACK_PROJECT_KEY=None,
                     AGENTSTACK_GEMINI_RESOURCES="src/**",
                     AGENTSTACK_GEMINI_TASK_FILE=task,
                     AGENTSTACK_WORKTREE_ROOT=world["tmp"] / "worktrees")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == CHILD
    assert [r["tool"] for r in mail.requests] == ["whois"]
    helpers = (world["tmp"] / "gemini-helpers.log").read_text(encoding="utf-8")
    assert "agentstack-gemini-child-mail reserve" in helpers
    assert f"--project-key {world['alpha']}" in helpers
    assert "new-session" in tmux_calls(world)
    assert not one_shot.exists(), "the started child consumed the handoff"
    assert token_file(world).read_text(encoding="utf-8") == TOKEN
