"""Explicit launchd spawn policy and verification, without starting a real agent."""
from __future__ import annotations

from dataclasses import replace
import http.client
import json
import os
import plistlib
import sys
import time

import pytest

from chatgpt_gateway_support import SRC
from test_chatgpt_gateway_service import (
    FakeLaunchctl, TARGET, paired, plan_for, release, start_like_launchd, stop,
)
from orrery_chatgpt_mcp import service
from orrery_chatgpt_mcp.config import ConfigError


def test_explicit_spawn_keeps_pinned_startup_and_message_default_off(tmp_path, release, paired):
    state, secret = paired
    work = tmp_path / "work"
    work.mkdir()
    plan = plan_for(release, state, spawn_dirs={"work": str(work)})
    spec = plistlib.loads(service.render_plist(plan))
    env = spec["EnvironmentVariables"]
    assert env["ORRERY_MCP_ENABLE_SPAWN"] == "1"
    assert env["ORRERY_MCP_SPAWN_DIRS"] == f"work={work}"
    assert env["ORRERY_MCP_SPAWN_APPROVAL"] == "local"
    assert env["ORRERY_MCP_ENABLE_MESSAGE"] == "0"
    assert spec["ProgramArguments"] == service.program_arguments(plan)
    assert spec["ProgramArguments"][1:5] == ["-I", "-S", "-B", "-c"]
    assert spec["RunAtLoad"] is True and spec["KeepAlive"] is True
    assert secret.encode() not in service.render_plist(plan)


@pytest.mark.parametrize("case", [
    "empty", "alias", "relative", "missing", "file", "nonstring", "comma", "approval",
])
def test_invalid_spawn_policy_leaves_running_service_untouched(tmp_path, release, paired, case):
    state, _ = paired
    work = tmp_path / "work"
    work.mkdir()
    regular_file = tmp_path / "file"
    regular_file.write_text("not a directory")
    comma = tmp_path / "comma,path"
    comma.mkdir()
    policies = {
        "empty": {}, "alias": {"BadAlias": str(work)}, "relative": {"work": "relative"},
        "missing": {"work": str(tmp_path / "missing")}, "file": {"work": str(regular_file)},
        "nonstring": {"work": True}, "comma": {"work": str(comma)},
        "approval": {"work": str(work)},
    }
    agents = tmp_path / "LA"
    agents.mkdir()
    original = plan_for(release, state)
    target = agents / TARGET
    target.write_bytes(service.render_plist(original))
    before = target.read_bytes()
    fake = FakeLaunchctl(loaded=True, path=str(target))
    plan = replace(original, spawn_dirs=policies[case],
                   spawn_approval="invalid" if case == "approval" else None)
    with pytest.raises(ConfigError):
        service.install_service(plan, runner=fake, launch_agents=agents, verify=False)
    assert not {"bootout", "bootstrap"}.intersection(fake.verbs())
    assert target.read_bytes() == before
    assert fake.loaded is True


def test_reinstall_preserves_approval_and_narrows_dirs_then_can_disable(tmp_path, release, paired):
    state, _ = paired
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    original = plan_for(release, state, spawn_dirs={"first": str(first), "second": str(second)},
                        spawn_approval="local")
    agents = tmp_path / "LA"
    agents.mkdir()
    target = agents / TARGET
    target.write_bytes(service.render_plist(original))
    fake = FakeLaunchctl(loaded=True, path=str(target))
    narrowed = replace(original, spawn_dirs={"first": str(first)}, spawn_approval=None)
    service.install_service(narrowed, runner=fake, launch_agents=agents, verify=False)
    env = plistlib.loads(target.read_bytes())["EnvironmentVariables"]
    assert env["ORRERY_MCP_SPAWN_DIRS"] == f"first={first}"
    assert env["ORRERY_MCP_SPAWN_APPROVAL"] == "local"
    service.install_service(replace(narrowed, spawn_approval="chat"), runner=fake,
                            launch_agents=agents, verify=False)
    assert plistlib.loads(target.read_bytes())["EnvironmentVariables"]["ORRERY_MCP_SPAWN_APPROVAL"] == "chat"
    service.install_service(replace(original, spawn_dirs=None, spawn_approval=None), runner=fake,
                            launch_agents=agents, verify=False)
    env = plistlib.loads(target.read_bytes())["EnvironmentVariables"]
    assert env["ORRERY_MCP_ENABLE_SPAWN"] == "0"
    assert "ORRERY_MCP_SPAWN_DIRS" not in env and "ORRERY_MCP_SPAWN_APPROVAL" not in env


def test_cli_passes_explicit_dirs_without_changing_unspecified_approval(tmp_path, paired, monkeypatch):
    from orrery_chatgpt_mcp import server

    state, _ = paired
    seen = []
    monkeypatch.setattr(service, "install_service", lambda plan: seen.append(plan) or {})
    assert server._service_cli([
        "install", "--from", str(SRC), "--python", sys.executable,
        "--spawn-dirs", f"work={tmp_path}",
    ], state) == 0
    assert seen[0].spawn_dirs == {"work": str(tmp_path)}
    assert seen[0].spawn_approval is None


@pytest.mark.parametrize("tail", [
    ["--spawn-dirs", "work=/tmp,work=/var"],
    ["--spawn-dirs", ""],
    ["--spawn-dirs", "work"],
    ["--spawn-approval", "chat"],
])
def test_cli_rejects_ambiguous_policy_before_install(tmp_path, paired, monkeypatch, tail):
    from orrery_chatgpt_mcp import server

    state, _ = paired
    seen = []
    monkeypatch.setattr(service, "install_service", lambda plan: seen.append(plan) or {})
    with pytest.raises(ConfigError):
        server._service_cli(["install", "--from", str(SRC), "--python", sys.executable, *tail], state)
    assert not seen


@pytest.mark.parametrize("change,accepted", [
    ({}, True),
    ({"spawn_enabled_on_server": False}, False),
    ({"spawn_enabled_on_server": 1}, False),
    ({"spawn": False}, False),
    ({"spawn_approval": "chat"}, False),
    ({"directories": ["work", "extra"]}, False),
    ({"directories": ["work", "work"]}, False),
    ({"directories": []}, False),
])
def test_probe_requires_exact_enabled_spawn_policy(monkeypatch, change, accepted):
    status = {
        "gateway": {"name": "orrery-chatgpt-mcp"}, "connection": {"auth_mode": "pairing"},
        "capabilities": {"spawn_enabled_on_server": True, "spawn": True, "spawn_approval": "local"},
        "spawn_directories": change.get("directories", ["work"]),
    }
    status["capabilities"].update({k: v for k, v in change.items() if k != "directories"})

    class Response:
        status = 200

        def read(self, _limit):
            return json.dumps({"result": {"structuredContent": status}}).encode()

    class Connection:
        sock = object()

        def connect(self):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(service.http.client, "HTTPConnection", lambda *_args, **_kwargs: Connection())
    monkeypatch.setattr(service, "_accepted_by", lambda *_args: True)
    kwargs = {"expected_spawn": True, "expected_spawn_approval": "local", "expected_spawn_directories": ["work"]}
    if accepted:
        assert service.probe_status(8790, "s" * 43, os.getpid(), **kwargs) == status
    else:
        with pytest.raises(service.VerifyError):
            service.probe_status(8790, "s" * 43, os.getpid(), **kwargs)


def test_pinned_process_exposes_spawn_without_starting_an_agent(tmp_path, release, paired, dashboard):
    state, secret = paired
    plan = plan_for(release, state, dashboard_url=dashboard.url,
                    spawn_dirs={"work": str(tmp_path)}, spawn_approval="chat")
    proc = start_like_launchd(plan, tmp_path)

    def rpc(method, params=None):
        conn = http.client.HTTPConnection("127.0.0.1", plan.port, timeout=1)
        try:
            conn.request("POST", f"/mcp/{secret}", json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
            }), {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
            response = conn.getresponse()
            assert response.status == 200
            return json.loads(response.read())["result"]
        finally:
            conn.close()

    try:
        deadline = time.monotonic() + 15
        while True:
            assert proc.poll() is None, "pinned gateway exited before readiness"
            try:
                status = rpc("tools/call", {"name": "orrery_status", "arguments": {}})["structuredContent"]
                break
            except OSError:
                assert time.monotonic() < deadline, "pinned gateway did not start"
                time.sleep(0.05)
        assert status["capabilities"]["spawn_enabled_on_server"] is True
        assert status["capabilities"]["spawn"] is True
        assert status["capabilities"]["spawn_approval"] == "chat"
        assert status["spawn_directories"] == ["work"]
        names = {item["name"] for item in rpc("tools/list")["tools"]}
        assert names == {"orrery_status", "orrery_list_agents", "orrery_get_agent", "orrery_spawn_status",
                         "orrery_spawn_preview", "orrery_spawn_commit"}
        assert dashboard.posts(real_only=True) == []
    finally:
        stop(proc)
