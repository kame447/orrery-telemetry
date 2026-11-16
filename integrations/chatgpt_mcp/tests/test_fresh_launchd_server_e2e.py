"""Opt-in real macOS launchd + fresh MCP SDK/Mail integration; fixture-only.

Only a uniquely named, non-autostart LaunchAgent is ever registered. No live
gateway, Tunnel, Mail instance, or real agent is accessed or modified.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

from fastmcp import Client
import pytest

from orrery_chatgpt_mcp.readonly_server import Config, pair, read_secret
from test_fresh_message_e2e import live, stored
from test_fresh_server_process_e2e import _dashboard, _unused_port


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MCP_FRESH_LAUNCHD_E2E") != "1",
    reason="explicit opt-in and interactive macOS login required for real fresh launchd E2E",
)


def _launchctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *arguments], capture_output=True, text=True,
        timeout=15, check=False,
    )


def _pid(output: str) -> int | None:
    match = re.search(r"(?m)^\s*pid = ([0-9]+)$", output)
    return int(match.group(1)) if match else None


def _exists(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def test_fresh_mcp_real_launchagent_sdk_mail_and_teardown(live, tmp_path: Path):
    label = "org.agentstack.chatgpt-mcp-fresh.fixture-e2e-" + uuid4().hex[:16]
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    assert _launchctl("print", target).returncode != 0

    with _dashboard(live.instance) as (dashboard_port, _):
        port = _unused_port()
        config = Config("fresh", f"http://127.0.0.1:{dashboard_port}", port,
                        live.root / "launchd-mcp-state")
        pair(config)
        secret = read_secret(config)
        url = f"http://127.0.0.1:{port}/mcp/{secret}"

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True, mode=0o700)
        plist = launch_agents / f"{label}.plist"
        log = tmp_path / "fresh-launchd.log"
        log.touch(mode=0o600)
        environment = {
            "HOME": live.env["HOME"],
            "PATH": live.env.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": live.env["PYTHONPATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
            "AGENTSTACK_LABEL_PREFIX": live.env["AGENTSTACK_LABEL_PREFIX"],
            "AGENTSTACK_MAIL_TOOLS_LOG_ENABLED": "false",
            "ORRERY_MCP_MODE": "fresh",
            "ORRERY_MCP_ENABLE_MESSAGE": "1",
            "ORRERY_MCP_ENABLE_SPAWN": "0",
            "ORRERY_MCP_PORT": str(port),
            "ORRERY_MCP_STATE_DIR": str(config.state_dir),
            "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(live.policy),
            "ORRERY_MCP_DASHBOARD_URL": config.dashboard_url,
        }
        definition = {
            "Label": label,
            "ProgramArguments": [
                sys.executable, "-m", "orrery_chatgpt_mcp.fresh_server", "serve",
            ],
            "WorkingDirectory": str(live.root),
            "EnvironmentVariables": environment,
            "RunAtLoad": False,
            "KeepAlive": False,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
        with plist.open("wb") as handle:
            plistlib.dump(definition, handle)
        plist.chmod(0o600)
        assert plist.stat().st_mode & 0o777 == 0o600
        assert definition["RunAtLoad"] is False
        assert definition["KeepAlive"] is False
        assert "ORRERY_MCP_PAIRING_SECRET" not in environment

        attempted = False
        old_pid = None
        observed_pids: set[int] = set()
        try:
            attempted = True
            boot = _launchctl("bootstrap", domain, str(plist))
            assert boot.returncode == 0, "private fresh bootstrap failed"
            before = _launchctl("print", target)
            assert before.returncode == 0
            assert f"path = {plist.resolve()}" in before.stdout
            assert "type = LaunchAgent" in before.stdout
            assert "state = not running" in before.stdout
            start = _launchctl("kickstart", target)
            assert start.returncode == 0, "private fresh kickstart failed"

            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                observed = _launchctl("print", target)
                if observed.returncode == 0 and _pid(observed.stdout):
                    old_pid = _pid(observed.stdout)
                    assert f"path = {plist.resolve()}" in observed.stdout
                    assert "state = running" in observed.stdout
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        pass
                time.sleep(0.1)
            else:
                pytest.fail("fresh LaunchAgent did not listen with an attested PID")

            assert old_pid is not None and _exists(old_pid) is True
            observed_pids.add(old_pid)

            async def check():
                async with Client(url) as client:
                    names = {tool.name for tool in await client.list_tools()}
                    assert names == {
                        "orrery_status", "orrery_list_agents", "orrery_get_agent",
                        "orrery_message_preview", "orrery_message_commit",
                        "orrery_message_status",
                    }
                    status = (await client.call_tool("orrery_status", {})).data
                    assert status["dashboard"]["mail_instance_id"] == live.instance
                    assert status["adapter"]["message_enabled_on_server"] is True
                    assert stored(live)[0] == []
                    preview = (await client.call_tool(
                        "orrery_message_preview", live.args)).data
                    assert preview["message"]["body_md"] == live.args["body_md"]
                    request = {
                        "preview_id": preview["preview_id"],
                        "content_digest": preview["content_digest"],
                        "idempotency_key": live.args["idempotency_key"],
                    }
                    accepted = (await client.call_tool(
                        "orrery_message_commit", request)).data
                    assert accepted["status"] == "accepted"
                    assert accepted["execution_status"] == "unobserved"
                    assert (await client.call_tool(
                        "orrery_message_status",
                        {"idempotency_key": live.args["idempotency_key"]},
                    )).data == accepted
                    assert (await client.call_tool(
                        "orrery_message_commit", request)).data == accepted
                    return request, accepted

            request, accepted = asyncio.run(check())
            confirmed = _launchctl("print", target)
            assert confirmed.returncode == 0
            assert _pid(confirmed.stdout) == old_pid
            assert f"path = {plist.resolve()}" in confirmed.stdout

            # First remove the exact fixture job, then verify its old PID is gone.
            # Do not restart when the outcome of bootout is uncertain.
            first_stop = _launchctl("bootout", target)
            assert first_stop.returncode == 0
            attempted = False
            assert _launchctl("print", target).returncode != 0
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and _exists(old_pid) is True:
                time.sleep(0.1)
            assert _exists(old_pid) is False, "original fresh MCP PID survived bootout"

            # One restart only, reusing the same fixture plist, state and Mail DB.
            # Never restart a production job or retry an unknown bootstrap.
            attempted = True
            assert _launchctl("bootstrap", domain, str(plist)).returncode == 0
            registration = _launchctl("print", target)
            assert registration.returncode == 0
            assert f"path = {plist.resolve()}" in registration.stdout
            assert "state = not running" in registration.stdout
            assert _launchctl("kickstart", target).returncode == 0

            new_pid = None
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                observed = _launchctl("print", target)
                if observed.returncode == 0 and _pid(observed.stdout):
                    new_pid = _pid(observed.stdout)
                    observed_pids.add(new_pid)
                    assert f"path = {plist.resolve()}" in observed.stdout
                    assert "state = running" in observed.stdout
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        pass
                time.sleep(0.1)
            else:
                pytest.fail("restarted fresh LaunchAgent did not listen")
            assert new_pid is not None and new_pid != old_pid
            assert _exists(old_pid) is False
            assert _exists(new_pid) is True

            async def check_after_restart():
                async with Client(url) as client:
                    status = (await client.call_tool("orrery_status", {})).data
                    assert status["dashboard"]["mail_instance_id"] == live.instance
                    assert status["adapter"]["message_enabled_on_server"] is True
                    saved = (await client.call_tool(
                        "orrery_message_status",
                        {"idempotency_key": live.args["idempotency_key"]},
                    )).data
                    assert saved == accepted
                    assert saved["execution_status"] == "unobserved"
                    assert (await client.call_tool(
                        "orrery_message_commit", request,
                    )).data == accepted

            asyncio.run(check_after_restart())
            assert len(stored(live)[0]) == 1, "restart replayed a Mail message"
        finally:
            # If bootstrap timed out, its outcome may be unknown: do not repeat.
            # One exact-label teardown is required before declaring this fixture safe.
            if attempted:
                stopped = _launchctl("bootout", target)
                assert stopped.returncode == 0, "fixture cleanup uncertain: bootout failed"
            assert _launchctl("print", target).returncode != 0
            for pid in observed_pids:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and _exists(pid) is True:
                    time.sleep(0.1)
                assert _exists(pid) is False, "fresh fixture PID still exists or is unknown"
            output = log.read_text()
            assert secret not in output
            assert live.args["body_md"] not in output
            assert "registration_token" not in output

        messages, recipients = stored(live)
        assert len(messages) == 1
        assert messages[0][1:] == (
            live.people[0]["agent_id"], live.args["subject"], live.args["body_md"],
        )
        assert recipients == [(messages[0][0], live.people[1]["agent_id"])]
