"""Opt-in Mac acceptance: killed private pinned LaunchAgent, explicit restart, receipt.

Only a verified fixture PID can be signalled. KeepAlive is disabled, and the
test never touches the installed gateway, Mail, Tunnel or real agents.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import plistlib
import signal
import socket
import sys
import time
from uuid import uuid4

from fastmcp import Client
import pytest

from fresh_launchd_cleanup import cleanup_launchagent
from orrery_chatgpt_mcp import service
from orrery_chatgpt_mcp.fresh_process_probe import Authority, probe
from orrery_chatgpt_mcp.fresh_runtime_command import build_command
from orrery_chatgpt_mcp.readonly_server import Config, pair, read_secret
from test_fresh_import_attest import case
from test_fresh_launchd_server_e2e import _exists, _launchctl, _pid
from test_fresh_message_e2e import live, stored
from test_fresh_pinned_runtime_e2e import _pinned_bundle
from test_fresh_server_process_e2e import _dashboard, _unused_port


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MCP_FRESH_PINNED_CRASH_E2E") != "1",
    reason="explicit opt-in for a verified fixture-only Mac SIGKILL and restart",
)


def _await_attested(target: str, label: str, plist: Path, port: int,
                    secret: str, expected: Authority, *, observed_pids: set[int],
                    previous: int | None = None) -> int:
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        record = _launchctl("print", target)
        if record.returncode == 0:
            pid = _pid(record.stdout)
            if pid is not None and pid != previous:
                observed_pids.add(pid)
                assert f"path = {plist.resolve()}" in record.stdout
                if _exists(pid) is True:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            attested = probe(
                                label=label, plist_path=str(plist.resolve()),
                                port=port, pairing_secret=secret, expected=expected,
                            )
                            assert attested["verified"] is True
                            assert attested["pid"] == pid
                            assert attested["message_enabled_on_server"] is True
                            assert attested["spawn_enabled_on_server"] is False
                            return pid
                    except OSError:
                        pass
        time.sleep(0.1)
    pytest.fail("private pinned LaunchAgent failed to become attested and ready")


def _await_gone(pid: int) -> None:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and _exists(pid) is True:
        time.sleep(0.1)
    assert _exists(pid) is False, "fixture PID failed to exit or cannot be verified"


def test_private_pinned_release_crash_then_explicit_restart_preserves_receipt(live, tmp_path: Path):
    label = "org.agentstack.chatgpt-mcp-fresh.fixture-crash-" + uuid4().hex[:16]
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    assert _launchctl("print", target).returncode != 0

    artifacts = tmp_path / "pinned-artifacts"
    artifacts.mkdir(mode=0o700)
    plan, dependencies, _ = case(artifacts)
    site, manifest = _pinned_bundle(artifacts)
    with _dashboard(live.instance) as (dashboard_port, _):
        port = _unused_port()
        cfg = Config("fresh", f"http://127.0.0.1:{dashboard_port}",
                     port, live.root / "crash-mcp-state")
        pair(cfg)
        secret = read_secret(cfg)
        plan = replace(
            plan, label=label, port=port, state_dir=cfg.state_dir,
            policy_path=live.policy, mail_instance_id=live.instance,
            sender_agent_id=live.people[0]["agent_id"],
            recipients=((live.people[1]["agent_id"], "Recipient", "to"),),
            dashboard_url=cfg.dashboard_url,
        )
        dependencies = replace(
            dependencies, site_packages=site, dependency_manifest=manifest,
        )
        command = build_command(
            plan, dependencies, verify_release=service.verify_release,
            read_release=service.read_release,
        )
        assert command.requires_in_process_attestation
        assert command.argv[:5] == (str(plan.python), "-I", "-S", "-B", "-c")
        assert "PYTHONPATH" not in command.env()
        expected = Authority(
            live.instance, live.people[0]["agent_id"],
            ((live.people[1]["agent_id"], "Recipient", "to"),),
        )

        agents = tmp_path / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, mode=0o700)
        plist = agents / f"{label}.plist"
        log = tmp_path / "crash-launchagent.log"
        log.touch(mode=0o600)
        definition = {
            "Label": label,
            "ProgramArguments": list(command.argv),
            "WorkingDirectory": str(live.root),
            "EnvironmentVariables": command.env(),
            "RunAtLoad": False,
            "KeepAlive": False,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
        with plist.open("wb") as handle:
            plistlib.dump(definition, handle)
        plist.chmod(0o600)
        assert plist.stat().st_mode & 0o777 == 0o600
        assert not definition["RunAtLoad"] and not definition["KeepAlive"]

        attempted = False
        observed_pids: set[int] = set()
        try:
            attempted = True
            assert _launchctl("bootstrap", domain, str(plist)).returncode == 0
            assert _launchctl("kickstart", target).returncode == 0
            first_pid = _await_attested(
                target, label, plist, port, secret, expected,
                observed_pids=observed_pids,
            )
            url = f"http://127.0.0.1:{port}/mcp/{secret}"

            async def send_once():
                async with Client(url) as client:
                    preview = (await client.call_tool(
                        "orrery_message_preview", live.args,
                    )).data
                    request = {key: preview[key] for key in
                               ("preview_id", "content_digest")}
                    request["idempotency_key"] = live.args["idempotency_key"]
                    accepted = (await client.call_tool(
                        "orrery_message_commit", request,
                    )).data
                    assert accepted["status"] == "accepted"
                    assert accepted["execution_status"] == "unobserved"
                    return request, accepted

            request, accepted = asyncio.run(send_once())
            assert len(stored(live)[0]) == 1

            # The only process we signal is the just-attested fixture PID.
            # A missing/changed job identity must abort before signalling.
            before = _launchctl("print", target)
            assert before.returncode == 0
            assert f"path = {plist.resolve()}" in before.stdout
            assert _pid(before.stdout) == first_pid
            assert probe(
                label=label, plist_path=str(plist.resolve()), port=port,
                pairing_secret=secret, expected=expected,
            )["pid"] == first_pid
            os.kill(first_pid, signal.SIGKILL)
            _await_gone(first_pid)
            time.sleep(1.0)
            passive = _launchctl("print", target)
            assert passive.returncode == 0
            assert _pid(passive.stdout) is None, "fixture restarted without explicit consent"
            with socket.socket() as client:
                client.settimeout(0.5)
                assert client.connect_ex(("127.0.0.1", port)) != 0

            assert _launchctl("kickstart", target).returncode == 0
            second_pid = _await_attested(
                target, label, plist, port, secret, expected, previous=first_pid,
                observed_pids=observed_pids,
            )
            assert second_pid != first_pid
            assert _exists(first_pid) is False

            async def reconcile():
                async with Client(url) as client:
                    persisted = (await client.call_tool(
                        "orrery_message_status",
                        {"idempotency_key": live.args["idempotency_key"]},
                    )).data
                    assert persisted == accepted
                    repeated = (await client.call_tool(
                        "orrery_message_commit", request,
                    )).data
                    assert repeated == accepted

            asyncio.run(reconcile())
            assert len(stored(live)[0]) == 1, "crash recovery duplicated Mail delivery"
        finally:
            cleanup_launchagent(
                target=target, attempted=attempted, observed_pids=observed_pids,
                launchctl=_launchctl, pid_exists=_exists, port=port, log_path=log,
                forbidden=(secret, live.args["body_md"], "registration_token"),
                body_error=sys.exception(),
            )
