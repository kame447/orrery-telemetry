"""Security boundary of the ChatGPT-facing gateway, over real HTTP."""
from __future__ import annotations

import http.client
import json
import logging
import os
import stat

import pytest

from chatgpt_gateway_support import ISSUER, OPERATOR, RESOURCE  # noqa: F401


def post(config, body, *, token=None, headers=None, raw=None, path=None):
    conn = http.client.HTTPConnection("127.0.0.1", config.port, timeout=10)
    data = raw if raw is not None else json.dumps(body).encode()
    h = {"Host": f"127.0.0.1:{config.port}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    conn.request("POST", path or config.mcp_path, body=data, headers=h)
    resp = conn.getresponse()
    text = resp.read()
    conn.close()
    return resp.status, dict(resp.getheaders()), (json.loads(text) if text else None)


def get(config, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", config.port, timeout=10)
    h = {"Host": f"127.0.0.1:{config.port}"}
    h.update(headers or {})
    conn.request("GET", path, headers=h)
    resp = conn.getresponse()
    text = resp.read()
    conn.close()
    return resp.status, dict(resp.getheaders()), (json.loads(text) if text else None)


def rpc(method, params=None, id=1):
    msg = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def call(config, token, tool, args=None, id=1):
    status, _, body = post(config, rpc("tools/call", {"name": tool, "arguments": args or {}}, id), token=token)
    assert status == 200, body
    return body["result"]


# -- authentication ----------------------------------------------------------

def test_unauthenticated_initialize_gets_oauth_challenge(make_gateway):
    config, _ = make_gateway()
    status, headers, body = post(config, rpc("initialize", {"protocolVersion": "2025-06-18"}))
    assert status == 401
    challenge = headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'resource_metadata="https://orrery.example.test/.well-known/oauth-protected-resource/mcp"' in challenge
    assert "result" not in (body or {})


def test_protected_resource_metadata_is_public_and_exact(make_gateway):
    config, _ = make_gateway()
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        status, _, body = get(config, path)
        assert status == 200
        assert body["resource"] == RESOURCE
        assert body["authorization_servers"] == [ISSUER]
        assert set(body["scopes_supported"]) == {"orrery.read", "orrery.spawn"}


@pytest.mark.parametrize("case", ["forged", "wrong_aud", "wrong_iss", "expired", "too_long", "future_iat", "unknown_kid", "malformed", "basic"])
def test_bad_tokens_are_rejected(make_gateway, keys, case):
    config, _ = make_gateway()
    token = {
        "forged": lambda: keys.mint(key=keys.other),
        "wrong_aud": lambda: keys.mint(aud="https://other.example.test/mcp"),
        "wrong_iss": lambda: keys.mint(iss="https://evil.example.test/"),
        "expired": lambda: keys.mint(iat_offset=-4000, lifetime=600),
        "too_long": lambda: keys.mint(lifetime=30 * 24 * 3600),
        "future_iat": lambda: keys.mint(iat_offset=3600),
        "unknown_kid": lambda: keys.mint(kid="k2"),
        "malformed": lambda: "not.a.jwt",
        "basic": lambda: None,
    }[case]()
    headers = {"Authorization": "Basic dXNlcjpwYXNz"} if case == "basic" else None
    status, hdrs, body = post(config, rpc("tools/list"), token=token, headers=headers)
    assert status == 401, (case, body)
    assert "WWW-Authenticate" in hdrs
    assert "result" not in body


def test_alg_none_and_hmac_confusion_are_rejected(make_gateway, keys):
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization

    config, _ = make_gateway()
    none_token = pyjwt.encode({"sub": OPERATOR, "aud": RESOURCE, "iss": ISSUER, "iat": 1, "exp": 2**31, "scope": "orrery.read"},
                              None, algorithm="none", headers={"kid": "k1"})
    pem = keys.private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    import hmac, hashlib, base64  # noqa: E401

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "kid": "k1", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": OPERATOR, "aud": RESOURCE, "iss": ISSUER, "iat": 1, "exp": 2**31, "scope": "orrery.read"}).encode())
    sig = b64(hmac.new(pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    for token in (none_token, f"{header}.{payload}.{sig}"):
        status, _, _ = post(config, rpc("tools/list"), token=token)
        assert status == 401


def test_unlisted_subject_and_missing_scope_are_forbidden(make_gateway, keys):
    config, _ = make_gateway()
    status, hdrs, _ = post(config, rpc("tools/list"), token=keys.mint(sub="user|someone-else"))
    assert status == 403
    status, hdrs, _ = post(config, rpc("tools/list"), token=keys.mint(scope="openid email"))
    assert status == 403
    assert 'error="insufficient_scope"' in hdrs["WWW-Authenticate"]


def test_mail_owner_style_token_is_not_accepted(make_gateway):
    config, _ = make_gateway()
    status, _, _ = post(config, rpc("tools/list"), token="a" * 43)
    assert status == 401


# -- transport admission -----------------------------------------------------

def test_dns_rebinding_host_and_cross_origin_are_refused(make_gateway, keys):
    config, _ = make_gateway()
    token = keys.mint()
    status, _, _ = post(config, rpc("tools/list"), token=token, headers={"Host": "evil.example:80"})
    assert status == 403
    status, _, _ = post(config, rpc("tools/list"), token=token, headers={"Origin": "https://evil.example"})
    assert status == 403
    status, _, _ = get(config, "/.well-known/oauth-protected-resource", {"Host": "attacker.test"})
    assert status == 403
    # The public resource host is accepted (reverse proxy / tunnel forwarding).
    status, _, _ = post(config, rpc("tools/list"), token=token, headers={"Host": "orrery.example.test"})
    assert status == 200


def test_body_limits_batches_and_protocol_versions(make_gateway, keys):
    config, _ = make_gateway()
    token = keys.mint()
    status, _, _ = post(config, None, token=token, raw=b"{" + b" " * (70 * 1024) + b"}")
    assert status == 413
    status, _, body = post(config, [rpc("ping"), rpc("ping", id=2)], token=token)
    assert body["error"]["code"] == -32600
    status, _, _ = post(config, rpc("ping"), token=token, headers={"MCP-Protocol-Version": "1999-01-01"})
    assert status == 400
    status, _, body = post(config, rpc("resources/read", {"uri": "file:///etc/passwd"}), token=token)
    assert body["error"]["code"] == -32601
    status, _, body = post(config, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=token)
    assert status == 202 and body is None
    status, _, body = post(config, None, token=token, raw=b"{not json")
    assert status == 400 and body["error"]["code"] == -32700
    status, _, _ = post(config, rpc("ping"), token=token, headers={"Content-Type": "text/plain"})
    assert status == 415


def test_get_on_mcp_is_405_and_unknown_paths_404(make_gateway, keys):
    config, _ = make_gateway()
    assert get(config, "/mcp")[0] == 405
    assert get(config, "/api/agents")[0] == 404
    status, _, _ = post(config, rpc("ping"), token=keys.mint(), path="/api/spawn")
    assert status == 404


def test_rate_limit_per_subject(make_gateway, keys):
    config, _ = make_gateway(rate_per_minute=3)
    token = keys.mint()
    codes = [post(config, rpc("ping"), token=token)[0] for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


def test_tokens_never_reach_logs(make_gateway, keys, caplog):
    config, _ = make_gateway()
    token = keys.mint(sub="user|nobody")
    with caplog.at_level(logging.DEBUG, logger="orrery_chatgpt_mcp"):
        post(config, rpc("tools/list"), token=token)
        call(config, keys.mint(), "orrery_status")
    assert token not in caplog.text
    assert token.split(".")[1] not in caplog.text


# -- tool surface ------------------------------------------------------------

def test_read_tools_and_spawn_hidden_when_disabled(make_gateway, keys, dashboard):
    config, _ = make_gateway()
    token = keys.mint()
    status, _, body = post(config, rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}), token=token)
    assert body["result"]["protocolVersion"] == "2025-06-18"
    _, _, body = post(config, rpc("tools/list"), token=token)
    names = {t["name"] for t in body["result"]["tools"]}
    assert names == {"orrery_status", "orrery_list_agents", "orrery_get_agent", "orrery_spawn_status"}
    for tool in body["result"]["tools"]:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["securitySchemes"] == [{"type": "oauth2", "scopes": ["orrery.read"]}]
    result = call(config, keys.mint(scope="orrery.read orrery.spawn"), "orrery_spawn_commit", {"preview_id": "x" * 30, "idempotency_key": "abcdefgh"})
    assert result["isError"] and result["structuredContent"]["error"]["code"] == "unknown_tool"
    assert dashboard.posts(real_only=False) == []


def test_agent_projection_drops_internal_fields(make_gateway, keys):
    config, _ = make_gateway()
    result = call(config, keys.mint(), "orrery_list_agents", {"running_only": False})
    agents = result["structuredContent"]["agents"]
    assert [a["name"] for a in agents] == ["WindyFermi", "IdleBohr"]  # terminals excluded
    blob = json.dumps(result)
    for leaked in ("secret-flag", "pane title", "transcript", "/Users/x", "internal"):
        assert leaked not in blob
    idle = agents[1]
    assert idle["activity"] is None  # unknown values stay unknown
    assert len(idle["task"]) <= 301
    assert "untrusted" in result["structuredContent"]["note"]
    running = call(config, keys.mint(), "orrery_list_agents")["structuredContent"]["agents"]
    assert [a["name"] for a in running] == ["WindyFermi"]


def test_get_agent_validates_name(make_gateway, keys, dashboard):
    config, _ = make_gateway()
    token = keys.mint()
    assert call(config, token, "orrery_get_agent", {"name": "WindyFermi"})["structuredContent"]["agent"]["model"] == "gpt-5.6-sol"
    for bad in ("../etc", "Windy;rm -rf /", "a", "WindyFermi&name=x"):
        r = call(config, token, "orrery_get_agent", {"name": bad})
        assert r["isError"] and r["structuredContent"]["error"]["code"] == "invalid_arguments"
    r = call(config, token, "orrery_get_agent", {"name": "WindyFermi", "path": "/api/kill"})
    assert r["isError"]
    assert all(p.startswith("/api/agents") for m, p, _ in dashboard.requests if m == "GET")


def test_dashboard_down_is_reported_not_faked(make_gateway, keys):
    from chatgpt_gateway_support import free_port

    config, _ = make_gateway(dashboard_url=f"http://127.0.0.1:{free_port()}")
    token = keys.mint()
    status = call(config, token, "orrery_status")["structuredContent"]
    assert status["dashboard"]["reachable"] is False
    r = call(config, token, "orrery_list_agents")
    assert r["isError"] and r["structuredContent"]["error"]["code"] == "dashboard_unavailable"


def test_malformed_dashboard_answer_is_an_error(make_gateway, keys, dashboard):
    config, _ = make_gateway()
    dashboard.spawn_mode = "garbage-agents"
    r = call(config, keys.mint(), "orrery_list_agents")
    assert r["isError"] and r["structuredContent"]["error"]["code"] == "dashboard_unavailable"


def test_status_reports_no_messaging_capability(make_gateway, keys):
    config, _ = make_gateway()
    caps = call(config, keys.mint(), "orrery_status")["structuredContent"]["capabilities"]
    assert caps["message_existing_agent"] is False
    assert caps["spawn"] is False


# -- configuration -----------------------------------------------------------

@pytest.mark.parametrize("override,needle", [
    ({"bind_host": "0.0.0.0"}, "loopback"),
    ({"bind_host": "192.168.1.5"}, "loopback"),
    ({"dashboard_url": "http://10.0.0.2:8770"}, "dashboard"),
    ({"dashboard_url": "http://127.0.0.1:8770/api"}, "dashboard"),
    ({"allowed_subjects": frozenset()}, "SUBJECTS"),
    ({"algorithms": frozenset({"HS256"})}, "algorithms"),
    ({"spawn_enabled": True}, "SPAWN_DIRS"),
    ({"spawn_enabled": True, "spawn_dirs": {"Bad Alias": "/tmp"}}, "alias"),
    ({"spawn_enabled": True, "spawn_dirs": {"proj": "relative/path"}}, "absolute"),
])
def test_config_refuses_unsafe_settings(keys, override, needle):
    from orrery_chatgpt_mcp.config import ConfigError, GatewayConfig

    cfg = dict(auth_mode="oauth", resource=RESOURCE, issuer=ISSUER, allowed_subjects=frozenset({OPERATOR}), jwks_file=keys.jwks_file)
    cfg.update(override)
    with pytest.raises(ConfigError, match=needle):
        GatewayConfig(**cfg).validate()


def test_from_env_requires_auth_settings(keys):
    from orrery_chatgpt_mcp.config import ConfigError, GatewayConfig

    with pytest.raises(ConfigError, match="ORRERY_MCP_RESOURCE"):
        GatewayConfig.from_env({"ORRERY_MCP_AUTH": "oauth"})
    with pytest.raises(ConfigError, match="https"):
        GatewayConfig.from_env({"ORRERY_MCP_AUTH": "oauth", "ORRERY_MCP_RESOURCE": "http://orrery.example.test/mcp", "ORRERY_MCP_ISSUER": ISSUER,
                                "ORRERY_MCP_ALLOWED_SUBJECTS": OPERATOR, "ORRERY_MCP_JWKS_FILE": str(keys.jwks_file)})
    cfg = GatewayConfig.from_env({"ORRERY_MCP_AUTH": "oauth", "ORRERY_MCP_RESOURCE": RESOURCE, "ORRERY_MCP_ISSUER": ISSUER, "ORRERY_MCP_ALLOWED_SUBJECTS": OPERATOR,
                                  "ORRERY_MCP_JWKS_FILE": str(keys.jwks_file), "ORRERY_MCP_ENABLE_SPAWN": "1",
                                  "ORRERY_MCP_SPAWN_DIRS": "notes=/Users/x/notes", "AGENTSTACK_HOME": "/nonexistent-home"})
    assert cfg.spawn_dirs == {"notes": "/Users/x/notes"}
    assert str(cfg.state_dir) == "/nonexistent-home/runtime/chatgpt-mcp"
    assert cfg.spawn_enabled and cfg.bind_host == "127.0.0.1"


def test_main_refuses_to_start_without_config(monkeypatch, capsys):
    from orrery_chatgpt_mcp.server import main

    for k in list(os.environ):
        if k.startswith("ORRERY_MCP_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("ORRERY_MCP_STATE_DIR", "/nonexistent-orrery-state")
    assert main([]) == 2
    assert "refusing to start" in capsys.readouterr().err


def test_ledger_permissions(make_gateway, keys, tmp_path):
    config, _ = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(tmp_path)})
    token = keys.mint(scope="orrery.read orrery.spawn")
    pid = call(config, token, "orrery_spawn_preview", {"task": "t", "directory": "proj"})["structuredContent"]["preview_id"]
    call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "key-00000001"})
    ledger = config.state_dir / "spawn-ledger.json"
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.state_dir.stat().st_mode) == 0o700
    text = ledger.read_text()
    assert token not in text and pid not in text and "key-00000001" not in text
