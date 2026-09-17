"""`whois` can prove that a private credential owns an exact identity.

Delegated child lifecycle and notification delivery both ask the bundled
server "does this private token still own this name in this project?" before
they release, retire, or type into a pane. The ordinary tokenless directory
lookup is unchanged; supplying a token turns the call into that proof.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import sqlite3

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from agentstack_mail import app, config, db

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REGISTER_LIB = REPOSITORY_ROOT / "bin" / "lib" / "agentstack-register.sh"


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENTSTACK_MAIL_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv(
        "AGENTSTACK_MAIL_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'mail.sqlite3'}",
    )
    monkeypatch.setenv("AGENTSTACK_MAIL_STORAGE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("AGENTSTACK_MAIL_NOTIFICATIONS_ENABLED", "false")
    monkeypatch.setenv("AGENTSTACK_MAIL_TOOLS_LOG_ENABLED", "false")
    db.reset_database_state()
    config.clear_settings_cache()


async def _register(client: Client, project: str, name: str, token: str | None) -> dict[str, Any]:
    await client.call_tool("ensure_project", {"human_key": project})
    arguments: dict[str, Any] = {
        "project_key": project,
        "program": "claude-code",
        "model": "test-model",
        "name": name,
        "task_description": "owner proof test",
    }
    if token is not None:
        arguments["registration_token"] = token
    result = await client.call_tool("register_agent", arguments)
    return result.structured_content or result.data


def _unbind_credential(tmp_path: Path, name: str) -> None:
    """Make a row credential-less, as a legacy registration is."""
    with sqlite3.connect(tmp_path / "mail.sqlite3") as connection:
        connection.execute("UPDATE agents SET registration_token = NULL WHERE name = ?", (name,))
        assert connection.total_changes == 1


async def _whois(client: Client, project: str, name: str, token: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"project_key": project, "agent_name": name,
                                 "include_recent_commits": False}
    if token is not None:
        arguments["registration_token"] = token
    result = await client.call_tool("whois", arguments)
    return result.structured_content or result.data


def test_the_owner_token_proves_the_exact_identity(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    async def scenario() -> None:
        project = str(tmp_path / "alpha")
        async with Client(app.build_mcp_server()) as client:
            registered = await _register(client, project, "Brisk-Curie", "owner-token")
            # The server owns both the identity and the credential it returns.
            name = registered["name"]
            owner_token = registered.get("registration_token") or "owner-token"
            profile = await _whois(client, project, name, owner_token)
            assert profile["name"] == name
            assert "registration_token" not in json.dumps(profile)
            # The ordinary directory lookup is untouched.
            assert (await _whois(client, project, name))["name"] == name

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["wrong-token", "empty-token", "unbound-legacy-row"])
def test_only_a_matching_credential_answers_the_proof(monkeypatch, tmp_path, case):
    _configure(monkeypatch, tmp_path)

    async def scenario() -> None:
        project = str(tmp_path / "alpha")
        async with Client(app.build_mcp_server()) as client:
            registered = await _register(client, project, "Brisk-Curie", "owner-token")
            name = registered["name"]
            supplied = {"wrong-token": "someone-elses-token", "empty-token": "",
                        "unbound-legacy-row": "invented-token"}[case]
            if case == "unbound-legacy-row":
                # register_agent always stores a credential (minted when the
                # caller supplies none) and never echoes it, so an unbound row
                # is legacy data; no token may speak for it.
                _unbind_credential(tmp_path, name)
            with pytest.raises(ToolError) as refusal:
                await _whois(client, project, name, supplied)
            if supplied:
                assert supplied not in str(refusal.value)
            # The ordinary directory entry stays readable either way.
            assert (await _whois(client, project, name))["name"] == name

    asyncio.run(scenario())


def test_a_same_name_token_from_another_project_is_refused(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    async def scenario() -> None:
        alpha, beta = str(tmp_path / "alpha"), str(tmp_path / "beta")
        async with Client(app.build_mcp_server()) as client:
            registered_alpha = await _register(client, alpha, "Brisk-Curie", "alpha-token")
            registered_beta = await _register(client, beta, "Brisk-Curie", "beta-token")
            beta_token = registered_beta.get("registration_token") or "beta-token"
            assert registered_alpha["name"] == registered_beta["name"]
            with pytest.raises(ToolError):
                await _whois(client, alpha, registered_alpha["name"], beta_token)

    asyncio.run(scenario())


def test_soft_retirement_keeps_the_owner_proof(monkeypatch, tmp_path):
    """Retirement is not credential revocation.

    The bundled server preserves the row and its token when an agent retires,
    so its owner can still prove itself and finish releasing or cleaning up.
    The exposed surface also refuses to rebind a token-bound name to another
    credential, so an existing proof cannot be quietly replaced; what does
    invalidate it is the identity no longer existing under that credential.
    """
    _configure(monkeypatch, tmp_path)

    async def scenario() -> None:
        project = str(tmp_path / "alpha")
        async with Client(app.build_mcp_server()) as client:
            registered = await _register(client, project, "Brisk-Curie", "owner-token")
            name = registered["name"]
            owner_token = registered.get("registration_token") or "owner-token"
            await client.call_tool("retire_agent", {"project_key": project,
                                                    "agent_name": name,
                                                    "registration_token": owner_token})
            retired = await _whois(client, project, name, owner_token)
            assert retired["retired_at"], "a retired row keeps its credential"

            with pytest.raises(ToolError):
                await _register(client, project, name, "second-token")
            assert (await _whois(client, project, name, owner_token))["name"] == name

    asyncio.run(scenario())


def test_claiming_an_unbound_legacy_row_binds_exactly_one_credential(monkeypatch, tmp_path):
    """The one supported way an unbound identity gains an owner."""
    _configure(monkeypatch, tmp_path)

    async def scenario() -> None:
        project = str(tmp_path / "alpha")
        async with Client(app.build_mcp_server()) as client:
            registered = await _register(client, project, "Brisk-Curie", "first-token")
            name = registered["name"]
            _unbind_credential(tmp_path, name)
            with pytest.raises(ToolError):
                await _whois(client, project, name, "first-token")

            await _register(client, project, name, "claimed-token")
            assert (await _whois(client, project, name, "claimed-token"))["name"] == name
            with pytest.raises(ToolError):
                await _whois(client, project, name, "first-token")

    asyncio.run(scenario())


# --- the shell helper against a real bundled HTTP server ---------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_ready(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 40
    payload = json.dumps({"jsonrpc": "2.0", "id": "ready", "method": "tools/call",
                          "params": {"name": "health_check", "arguments": {}}}).encode()
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"server exited: {process.communicate()[1]}")
        request = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise AssertionError("bundled server did not become ready")


@pytest.fixture
def bundled_server(tmp_path):
    """The real bundled HTTP entry point on an isolated database and home."""
    port = _free_port()
    home = tmp_path / "home"
    home.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENTSTACK_")}
    env.update(
        HOME=str(home),
        PATH=f"{Path(sys.executable).parent}:/usr/bin:/bin",
        PYTHONPATH=str(REPOSITORY_ROOT / "packages" / "agentstack_mail" / "src"),
        AGENTSTACK_MAIL_ENV_FILE=str(tmp_path / "missing.env"),
        AGENTSTACK_MAIL_DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'service.sqlite3'}",
        AGENTSTACK_MAIL_STORAGE_ROOT=str(tmp_path / "archive"),
        AGENTSTACK_MAIL_NOTIFICATIONS_ENABLED="false",
        AGENTSTACK_MAIL_TOOLS_LOG_ENABLED="false",
        AGENTSTACK_MAIL_HTTP_HOST="127.0.0.1",
        AGENTSTACK_MAIL_HTTP_PORT=str(port),
        AGENTSTACK_MAIL_HTTP_PATH="/mcp",
        AGENTSTACK_MAIL_AGENT_NAME_ENFORCEMENT_MODE="passthrough",
    )
    process = subprocess.Popen([sys.executable, "-m", "agentstack_mail.cli"], env=env,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        _wait_ready(url, process)
        yield {"url": url, "env": env, "home": home, "tmp": tmp_path}
    finally:
        process.terminate()
        try:
            process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


def _call(url: str, tool: str, arguments: dict) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": tool, "method": "tools/call",
                          "params": {"name": tool, "arguments": arguments}}).encode()
    request = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode()
    for line in body.splitlines():
        if line.startswith("data:"):
            body = line[5:].strip()
            break
    return json.loads(body)


def _verify_with_helper(server, project: str, name: str, token_file: Path) -> subprocess.CompletedProcess:
    """Run the real shell owner proof used by the child launchers/cleanup."""
    return subprocess.run(
        ["/bin/bash", "-c",
         '. "$1"; ags_verify_child_credential "$2" "$3" "$4"',
         "owner-proof", str(REGISTER_LIB), project, name, str(token_file)],
        env={**server["env"], "AGENTSTACK_MCP_URL": server["url"]},
        text=True, capture_output=True, timeout=60)


def test_the_shell_owner_proof_works_against_the_bundled_server(bundled_server):
    project = str(bundled_server["tmp"] / "alpha")
    _call(bundled_server["url"], "ensure_project", {"human_key": project})
    registered = _call(bundled_server["url"], "register_agent", {
        "project_key": project, "program": "claude-code", "model": "test-model",
        "name": "Brisk-Curie", "task_description": "owner proof over HTTP",
        "registration_token": "owner-token"})
    structured = (registered.get("result") or {}).get("structuredContent") or {}
    name = structured["name"]
    owner_token = structured.get("registration_token") or "owner-token"

    token_file = bundled_server["tmp"] / f"agent_token_{name}"
    token_file.write_text(owner_token, encoding="utf-8")
    token_file.chmod(0o600)
    accepted = _verify_with_helper(bundled_server, project, name, token_file)
    assert accepted.returncode == 0, accepted.stderr

    other = bundled_server["tmp"] / "agent_token_other"
    other.write_text("someone-elses-token", encoding="utf-8")
    other.chmod(0o600)
    refused = _verify_with_helper(bundled_server, project, name, other)
    assert refused.returncode != 0
    assert owner_token not in refused.stdout + refused.stderr

    # An unknown name in the same project is refused as well.
    missing = _verify_with_helper(bundled_server, project, "Absent-Bohr", token_file)
    assert missing.returncode != 0
