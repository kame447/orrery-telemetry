"""Opt-in macOS launchctl acceptance for an immutable fresh MCP runtime.

Registers only one unpredictable, non-autostart, fixture-specific LaunchAgent.
Neither the production gateway nor any other job is ever restarted or stopped.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import plistlib
import socket
import sys
import time
from uuid import uuid4

from fastmcp import Client
import pytest

from orrery_chatgpt_mcp import service
from orrery_chatgpt_mcp.fresh_dependency_attest import verify as verify_dependency
from orrery_chatgpt_mcp.fresh_process_probe import Authority, probe
from orrery_chatgpt_mcp.fresh_runtime_command import build_command
from orrery_chatgpt_mcp.readonly_server import Config, pair, read_secret
from test_fresh_import_attest import case
from test_fresh_launchd_server_e2e import _exists, _launchctl, _pid
from test_fresh_message_e2e import live, stored
from test_fresh_pinned_runtime_e2e import _pinned_bundle
from test_fresh_server_process_e2e import _dashboard, _unused_port


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MCP_FRESH_PINNED_LAUNCHD_E2E") != "1",
    reason="explicit opt-in, interactive Mac launchd and an immutable dependency copy required",
)


def test_pinned_fresh_launchagent_pid_effective_grants_sdk_and_mail(live, tmp_path: Path):
    label = "org.agentstack.chatgpt-mcp-fresh.fixture-pinned-" + uuid4().hex[:16]
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    assert _launchctl("print", target).returncode != 0

    artifacts = tmp_path / "pinned-artifacts"
    artifacts.mkdir(mode=0o700)
    plan, dependencies, _ = case(artifacts)
    site, manifest = _pinned_bundle(artifacts)
    with _dashboard(live.instance) as (dashboard_port, _):
        port = _unused_port()
        config = Config(
            "fresh", f"http://127.0.0.1:{dashboard_port}", port,
            live.root / "pinned-launchagent-state",
        )
        pair(config)
        plan = replace(
            plan, label=label, port=port, state_dir=config.state_dir,
            policy_path=live.policy, mail_instance_id=live.instance,
            sender_agent_id=live.people[0]["agent_id"],
            recipients=((live.people[1]["agent_id"], "Recipient", "to"),),
            dashboard_url=config.dashboard_url,
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
        assert command.env()["ORRERY_MCP_DASHBOARD_URL"] == config.dashboard_url
        assert "PYTHONPATH" not in command.env()
        assert verify_dependency(manifest, site)["site_packages_sha256"]
        assert service.verify_release(
            plan.release_dir, plan.release_digest, str(plan.python),
        )

        agents = tmp_path / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, mode=0o700)
        plist = agents / f"{label}.plist"
        output = tmp_path / "pinned-launchagent.log"
        output.touch(mode=0o600)
        definition = {
            "Label": label,
            "ProgramArguments": list(command.argv),
            "WorkingDirectory": str(live.root),
            "EnvironmentVariables": command.env(),
            "RunAtLoad": False,
            "KeepAlive": False,
            "StandardOutPath": str(output),
            "StandardErrorPath": str(output),
        }
        with plist.open("wb") as file:
            plistlib.dump(definition, file)
        plist.chmod(0o600)
        assert plist.stat().st_mode & 0o777 == 0o600
        assert not definition["RunAtLoad"] and not definition["KeepAlive"]
        assert all("SECRET" not in key and "TOKEN" not in key
                   for key in definition["EnvironmentVariables"])

        attempted = False
        started_pid = None
        try:
            attempted = True
            assert _launchctl("bootstrap", domain, str(plist)).returncode == 0
            registered = _launchctl("print", target)
            assert registered.returncode == 0
            assert f"path = {plist.resolve()}" in registered.stdout
            assert "state = not running" in registered.stdout
            assert _launchctl("kickstart", target).returncode == 0

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                observed = _launchctl("print", target)
                if observed.returncode == 0:
                    started_pid = _pid(observed.stdout) or started_pid
                if started_pid:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        pass
                time.sleep(0.1)
            else:
                pytest.fail("private pinned LaunchAgent never listened")
            assert started_pid is not None and _exists(started_pid) is True
            attested = probe(
                label=label, plist_path=str(plist.resolve()), port=port,
                pairing_secret=read_secret(config),
                expected=Authority(
                    live.instance, live.people[0]["agent_id"],
                    ((live.people[1]["agent_id"], "Recipient", "to"),),
                ),
            )
            assert attested["pid"] == started_pid
            assert attested["verified"] is True
            assert attested["spawn_enabled_on_server"] is False
            assert attested["message_enabled_on_server"] is True

            async def verify_sdk():
                url = f"http://127.0.0.1:{port}/mcp/{read_secret(config)}"
                async with Client(url) as client:
                    tools = {tool.name for tool in await client.list_tools()}
                    assert len(tools) == 6
                    preview = (await client.call_tool(
                        "orrery_message_preview", live.args,
                    )).data
                    assert preview["message"]["body_md"] == live.args["body_md"]
                    request = {
                        "preview_id": preview["preview_id"],
                        "content_digest": preview["content_digest"],
                        "idempotency_key": live.args["idempotency_key"],
                    }
                    accepted = (await client.call_tool(
                        "orrery_message_commit", request,
                    )).data
                    assert accepted["status"] == "accepted"
                    assert accepted["execution_status"] == "unobserved"
                    assert (await client.call_tool(
                        "orrery_message_status",
                        {"idempotency_key": live.args["idempotency_key"]},
                    )).data == accepted
                    assert (await client.call_tool(
                        "orrery_message_commit", request,
                    )).data == accepted
                    return accepted

            accepted = asyncio.run(verify_sdk())
            assert stored(live)[0] == [(
                accepted["receipt"]["message_id"],
                live.people[0]["agent_id"],
                live.args["subject"], live.args["body_md"],
            )]
            confirmation = _launchctl("print", target)
            assert confirmation.returncode == 0
            assert _pid(confirmation.stdout) == started_pid
            assert f"path = {plist.resolve()}" in confirmation.stdout
        finally:
            if attempted:
                # Never target another label or retry a possibly completed bootout.
                stopped = _launchctl("bootout", target)
                assert stopped.returncode == 0, "fixture shutdown unverified"
            assert _launchctl("print", target).returncode != 0
            if started_pid:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and _exists(started_pid) is True:
                    time.sleep(0.1)
                assert _exists(started_pid) is False
            with socket.socket() as client:
                client.settimeout(0.5)
                assert client.connect_ex(("127.0.0.1", port)) != 0
            log = output.read_text()
            assert read_secret(config) not in log
            assert live.args["body_md"] not in log
            assert "registration_token" not in log
