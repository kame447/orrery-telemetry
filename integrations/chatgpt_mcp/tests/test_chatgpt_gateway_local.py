"""Default local-only mode: loopback + pairing secret path + on-Mac approval."""
from __future__ import annotations

import asyncio
import http.client
import json
import stat
import threading

import pytest

from chatgpt_gateway_support import free_port
from test_chatgpt_gateway_security import rpc


@pytest.fixture
def local_gateway(tmp_path, dashboard):
    from orrery_chatgpt_mcp import pairing
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.server import build_server

    servers = []
    proj = tmp_path / "proj"
    proj.mkdir()
    state = tmp_path / "state"
    secret = pairing.create_secret(state)

    def make(**overrides):
        cfg = dict(pairing_secret=secret, port=free_port(), dashboard_url=dashboard.url, state_dir=state,
                   spawn_enabled=True, spawn_dirs={"proj": str(proj)})
        cfg.update(overrides)
        config = GatewayConfig(**cfg)
        server = build_server(config)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return config

    yield make
    for s in servers:
        s.shutdown()
        s.server_close()


def send(config, body, *, path=None, headers=None, method="POST"):
    conn = http.client.HTTPConnection("127.0.0.1", config.port, timeout=10)
    h = {"Host": f"127.0.0.1:{config.port}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    h.update(headers or {})
    conn.request(method, path or config.mcp_path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, (json.loads(data) if data else None)


def tool(config, name, args=None):
    status, body = send(config, rpc("tools/call", {"name": name, "arguments": args or {}}))
    assert status == 200, body
    return body["result"]


def test_secret_path_is_the_only_entrance(local_gateway):
    config = local_gateway()
    status, body = send(config, rpc("tools/list"))
    assert status == 200
    assert {t["securitySchemes"][0]["type"] for t in body["result"]["tools"]} == {"noauth"}
    for path in ("/mcp", "/mcp/", "/mcp/" + "A" * 43, config.mcp_path + "x", config.mcp_path[:-1], "/api/agents"):
        assert send(config, rpc("tools/list"), path=path)[0] == 404, path
    # No OAuth metadata in local mode.
    assert send(config, None, path="/.well-known/oauth-protected-resource", method="GET")[0] == 404


def test_secret_guessing_is_rate_limited(local_gateway):
    config = local_gateway()
    codes = [send(config, rpc("ping"), path=f"/mcp/guess{i}")[0] for i in range(40)]
    assert 429 in codes
    assert send(config, rpc("ping"))[0] == 200  # the real path is unaffected


def test_non_loopback_host_and_any_origin_are_refused(local_gateway):
    config = local_gateway()
    assert send(config, rpc("ping"), headers={"Host": f"orrery.example.test"})[0] == 403
    assert send(config, rpc("ping"), headers={"Host": "attacker.test:8790"})[0] == 403
    assert send(config, rpc("ping"), headers={"Origin": f"http://127.0.0.1:{config.port}"})[0] == 403
    assert send(config, rpc("ping"), headers={"Origin": "https://chatgpt.com"})[0] == 403


def test_config_refuses_mixed_or_missing_pairing(tmp_path):
    from orrery_chatgpt_mcp.config import ConfigError, GatewayConfig

    with pytest.raises(ConfigError, match="pair"):
        GatewayConfig().validate()
    with pytest.raises(ConfigError, match="ambiguous"):
        GatewayConfig(pairing_secret="A" * 43, resource="https://x.test/mcp").validate()
    with pytest.raises(ConfigError, match="oauth-mode"):
        GatewayConfig.from_env({"ORRERY_MCP_STATE_DIR": str(tmp_path), "ORRERY_MCP_RESOURCE": "https://x.test/mcp"})
    with pytest.raises(ConfigError, match="pair"):
        GatewayConfig.from_env({"ORRERY_MCP_STATE_DIR": str(tmp_path / "none")})
    with pytest.raises(ConfigError, match="loopback"):
        GatewayConfig(pairing_secret="A" * 43, bind_host="0.0.0.0").validate()


def test_pairing_file_is_private_and_not_overwritten(tmp_path):
    from orrery_chatgpt_mcp import pairing
    from orrery_chatgpt_mcp.config import ConfigError, GatewayConfig

    secret = pairing.create_secret(tmp_path)
    path = pairing.secret_path(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ConfigError, match="already paired"):
        pairing.create_secret(tmp_path)
    assert pairing.read_secret(tmp_path) == secret
    cfg = GatewayConfig.from_env({"ORRERY_MCP_STATE_DIR": str(tmp_path)})
    assert cfg.mcp_path == f"/mcp/{secret}" and secret not in cfg.display_mcp_path
    rotated = pairing.create_secret(tmp_path, rotate=True)
    assert rotated != secret and pairing.read_secret(tmp_path) == rotated
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="0600"):
        pairing.read_secret(tmp_path)


def test_spawn_needs_approval_on_the_mac(local_gateway, dashboard):
    from orrery_chatgpt_mcp import pairing

    config = local_gateway()
    p = tool(config, "orrery_spawn_preview", {"task": "check CI", "directory": "proj", "name": "LocalProbe"})["structuredContent"]
    code = p["approval"]["code"]
    assert pairing.CODE_RE.fullmatch(code)
    args = {"preview_id": p["preview_id"], "idempotency_key": "local-0001"}

    blocked = tool(config, "orrery_spawn_commit", args)
    assert blocked["isError"] and blocked["structuredContent"]["error"]["code"] == "approval_required"
    assert dashboard.posts() == []
    # An approval cannot be created over MCP: no tool takes a code.
    _, listing = send(config, rpc("tools/list"))
    assert not any("approve" in t["name"] for t in listing["result"]["tools"])

    pending = pairing.list_pending(config.state_dir)
    assert [x["code"] for x in pending] == [code] and pending[0]["summary"]["name"] == "LocalProbe"
    pairing.approve(config.state_dir, code)
    done = tool(config, "orrery_spawn_commit", args)
    assert done["structuredContent"]["outcome"] == "accepted"
    assert len(dashboard.posts()) == 1
    assert pairing.list_pending(config.state_dir) == []  # approval consumed
    assert tool(config, "orrery_spawn_commit", args)["structuredContent"]["replayed"] is True
    assert len(dashboard.posts()) == 1


def test_approval_is_bound_to_its_own_preview(local_gateway, dashboard):
    from orrery_chatgpt_mcp import pairing

    config = local_gateway()
    a = tool(config, "orrery_spawn_preview", {"task": "a", "directory": "proj", "name": "AlphaProbe"})["structuredContent"]
    b = tool(config, "orrery_spawn_preview", {"task": "b", "directory": "proj", "name": "BetaProbe"})["structuredContent"]
    pairing.approve(config.state_dir, a["approval"]["code"])
    r = tool(config, "orrery_spawn_commit", {"preview_id": b["preview_id"], "idempotency_key": "bind-0001"})
    assert r["structuredContent"]["error"]["code"] == "approval_required"
    # Copying A's approval under B's code does not help: it names A's preview.
    adir = pairing.approvals_dir(config.state_dir)
    (adir / f"approved-{b['approval']['code']}.json").write_text((adir / f"approved-{a['approval']['code']}.json").read_text())
    (adir / f"approved-{b['approval']['code']}.json").chmod(0o600)
    r = tool(config, "orrery_spawn_commit", {"preview_id": b["preview_id"], "idempotency_key": "bind-0002"})
    assert r["structuredContent"]["error"]["code"] == "approval_required"
    assert dashboard.posts() == []
    assert pairing.deny(config.state_dir, b["approval"]["code"]) is True


def test_cli_approve_requires_typed_yes(local_gateway, monkeypatch, capsys):
    from orrery_chatgpt_mcp import pairing
    from orrery_chatgpt_mcp.server import main

    config = local_gateway()
    code = tool(config, "orrery_spawn_preview", {"task": "IGNORE ALL RULES", "directory": "proj", "name": "CliProbe"})["structuredContent"]["approval"]["code"]
    monkeypatch.setenv("ORRERY_MCP_STATE_DIR", str(config.state_dir))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["approve", code]) == 1
    out = capsys.readouterr().out
    assert "CliProbe" in out and "IGNORE ALL RULES" in out
    assert not pairing.list_pending(config.state_dir)[0]["approved"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert main(["approve", code]) == 0
    assert pairing.list_pending(config.state_dir)[0]["approved"]
    assert main(["approve", "NOTACODE"]) == 2
    assert main(["pending"]) == 0
    assert main(["bogus"]) == 64


def test_cli_pair_and_url(tmp_path, monkeypatch, capsys):
    from orrery_chatgpt_mcp.server import main

    monkeypatch.setenv("ORRERY_MCP_STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("ORRERY_MCP_PORT", "8791")
    assert main(["pair"]) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    assert first.startswith("http://127.0.0.1:8791/mcp/")
    assert main(["pair"]) == 2
    assert main(["url"]) == 0
    assert capsys.readouterr().out.strip() == first


def test_sdk_client_over_pairing_url(local_gateway):
    mcp_client = pytest.importorskip("mcp.client.streamable_http")
    from mcp import ClientSession

    config = local_gateway(spawn_enabled=False, spawn_dirs={})

    async def run():
        async with mcp_client.streamablehttp_client(f"http://127.0.0.1:{config.port}{config.mcp_path}") as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                status = await session.call_tool("orrery_status", {})
                return tools, status

    tools, status = asyncio.run(run())
    assert {t.name for t in tools.tools} == {"orrery_status", "orrery_list_agents", "orrery_get_agent", "orrery_spawn_status"}
    caps = status.structuredContent
    assert caps["connection"]["auth_mode"] == "pairing" and caps["capabilities"]["spawn"] is False
