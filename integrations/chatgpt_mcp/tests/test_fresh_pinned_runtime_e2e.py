"""Opt-in pinned release + immutable dependency process, SDK and private Mail E2E."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import sysconfig
import time

from fastmcp import Client
import pytest

from orrery_chatgpt_mcp import service
from orrery_chatgpt_mcp.fresh_dependency_attest import inspect as inspect_bundle
from orrery_chatgpt_mcp.fresh_runtime_command import build_command
from orrery_chatgpt_mcp.readonly_server import Config, pair, read_secret
from test_fresh_dependency_attest import freeze
from test_fresh_import_attest import case
from test_fresh_message_e2e import live, stored
from test_fresh_server_process_e2e import _dashboard, _unused_port


pytestmark = pytest.mark.skipif(
    os.environ.get("MCP_FRESH_PINNED_RUNTIME_E2E") != "1",
    reason="explicit opt-in: copy and freeze a separate real third-party bundle",
)


def _pinned_bundle(root: Path) -> tuple[Path, Path]:
    source = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    site = root / "fixed-site"

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name == "__pycache__" or
                name.endswith((".pth", ".egg-link", ".pyc", ".pyo"))}

    shutil.copytree(source, site, ignore=ignore)
    freeze(site)
    measured = inspect_bundle(site)
    manifest = root / "fixed-site-pin.json"
    manifest.write_text(json.dumps({key: measured[key] for key in
                                   ("kind", "site_packages", "sha256", "distributions")}))
    manifest.chmod(0o600)
    return site, manifest


def test_fixed_release_in_process_attestation_real_sdk_and_mail(live, tmp_path: Path):
    preflight_root = tmp_path / "fixed-artifacts"
    preflight_root.mkdir(mode=0o700)
    plan, dependency, _ = case(preflight_root)
    site, manifest = _pinned_bundle(preflight_root)
    with _dashboard(live.instance) as (dashboard_port, _):
        port = _unused_port()
        state = live.root / "fixed-release-mcp-state"
        config = Config("fresh", f"http://127.0.0.1:{dashboard_port}", port, state)
        pair(config)
        plan = replace(
            plan, state_dir=state, policy_path=live.policy,
            label="org.agentstack.chatgpt-mcp-fresh.fixture-fixed-release",
            port=port, mail_instance_id=live.instance,
            sender_agent_id=live.people[0]["agent_id"],
            recipients=((live.people[1]["agent_id"], "Recipient", "to"),),
            dashboard_url=config.dashboard_url,
        )
        dependency = replace(dependency, site_packages=site, dependency_manifest=manifest)
        command = build_command(plan, dependency,
                                verify_release=service.verify_release,
                                read_release=service.read_release)
        assert command.requires_in_process_attestation is True
        assert command.argv[:5] == (str(plan.python), "-I", "-S", "-B", "-c")
        assert "PYTHONPATH" not in command.env()
        assert command.env()["ORRERY_MCP_DASHBOARD_URL"] == config.dashboard_url
        url = f"http://127.0.0.1:{port}/mcp/{read_secret(config)}"
        log = live.root / "fixed-release-mcp.log"
        with log.open("w") as output:
            log.chmod(0o600)
            process = subprocess.Popen(command.argv, env=command.env(), cwd=live.root,
                                       stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    assert process.poll() is None, "pinned runtime exited without readiness"
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    pytest.fail("pinned runtime never listened on isolated loopback")

                async def verify():
                    async with Client(url) as client:
                        names = {tool.name for tool in await client.list_tools()}
                        assert names == {"orrery_status", "orrery_list_agents", "orrery_get_agent",
                                         "orrery_message_preview", "orrery_message_commit",
                                         "orrery_message_status"}
                        status = (await client.call_tool("orrery_status", {})).data
                        assert status["dashboard"]["mail_instance_id"] == live.instance
                        assert status["adapter"]["message_enabled_on_server"] is True
                        preview = (await client.call_tool("orrery_message_preview", live.args)).data
                        assert preview["message"]["body_md"] == live.args["body_md"]
                        request = {key: preview[key] for key in ("preview_id", "content_digest")}
                        request["idempotency_key"] = live.args["idempotency_key"]
                        accepted = (await client.call_tool("orrery_message_commit", request)).data
                        assert accepted["status"] == "accepted"
                        assert accepted["execution_status"] == "unobserved"
                        assert (await client.call_tool("orrery_message_status", {
                            "idempotency_key": live.args["idempotency_key"]})).data == accepted
                        assert (await client.call_tool("orrery_message_commit", request)).data == accepted
                        return accepted

                accepted = asyncio.run(verify())
                assert stored(live)[0] == [(
                    accepted["receipt"]["message_id"], live.people[0]["agent_id"],
                    live.args["subject"], live.args["body_md"],
                )]
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=7)
                assert process.poll() is not None
                output.flush()
                contents = log.read_text()
                assert read_secret(config) not in contents
                assert live.args["body_md"] not in contents
                for person in live.people:
                    assert json.loads(Path(person["binding_path"]).read_text())[
                        "registration_token"] not in contents

        # Mutating only the disposable release after teardown must prevent a
        # second process from serving; no service mutation or retry occurs.
        gateway_source = plan.release_dir / "orrery_chatgpt_mcp" / "fresh_server.py"
        gateway_source.chmod(0o600)
        gateway_source.write_bytes(gateway_source.read_bytes() + b"\n# fixture tamper\n")
        gateway_source.chmod(0o444)
        assert service.verify_release(plan.release_dir, plan.release_digest,
                                      str(plan.python)) is False
        refused = subprocess.run(command.argv, env=command.env(), cwd=live.root,
                                 capture_output=True, text=True, timeout=15)
        assert refused.returncode == 78
        assert read_secret(config) not in refused.stdout + refused.stderr
        assert live.args["body_md"] not in refused.stdout + refused.stderr
        with socket.socket() as client:
            client.settimeout(0.5)
            assert client.connect_ex(("127.0.0.1", port)) != 0
