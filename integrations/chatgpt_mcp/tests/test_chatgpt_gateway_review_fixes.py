"""Regression tests for the independent review (REVIEW-astra F01-F12)."""
from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
import time

import pytest

from chatgpt_gateway_support import ISSUER, OPERATOR, RESOURCE, free_port
from test_chatgpt_gateway_local import local_gateway, send, tool  # noqa: F401  (fixture re-export)
from test_chatgpt_gateway_security import call, post, rpc

SPAWN = "orrery.read orrery.spawn"


def raw(config, data: bytes, *, wait=2.0) -> tuple[list[int], bool]:
    """Send raw bytes; return every HTTP status seen and whether the server closed the socket."""
    s = socket.create_connection(("127.0.0.1", config.port), timeout=wait)
    s.sendall(data)
    chunks, closed = b"", False
    deadline = time.time() + wait
    try:
        while time.time() < deadline:
            part = s.recv(65536)
            if not part:
                closed = True
                break
            chunks += part
    except socket.timeout:
        pass
    finally:
        s.close()
    statuses = [int(line.split()[1]) for line in chunks.split(b"\r\n") if line.startswith(b"HTTP/1.")]
    return statuses, closed


def _req(path, body: bytes, host, extra=b""):
    return (b"POST " + path.encode() + b" HTTP/1.1\r\nHost: " + host.encode() + b"\r\nContent-Type: application/json\r\n"
            + extra + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)


# -- F01 approval shows everything it authorises ------------------------------

def test_f01_approve_shows_full_task_and_rejects_terminal_controls(local_gateway, monkeypatch, capsys, dashboard):
    from orrery_chatgpt_mcp.server import main

    config = local_gateway()
    task = "harmless intro " * 20 + "HIDDEN_SUFFIX_MARKER do something else"
    assert len(task) > 300
    code = tool(config, "orrery_spawn_preview", {"task": task, "directory": "proj", "name": "SuffixProbe"})["structuredContent"]["approval"]["code"]
    monkeypatch.setenv("ORRERY_MCP_STATE_DIR", str(config.state_dir))
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    main(["approve", code])
    assert "HIDDEN_SUFFIX_MARKER" in capsys.readouterr().out
    for bad in ("clear\x1b[2Jscreen", "over\rwrite", "rtl‮evil", "bell\x07", "c1\x9b"):
        r = tool(config, "orrery_spawn_preview", {"task": bad, "directory": "proj"})
        assert r["structuredContent"]["error"]["code"] == "invalid_arguments", repr(bad)


def test_f01_safe_rendering_escapes_controls():
    from orrery_chatgpt_mcp.server import _safe

    assert _safe("a\x1b[2Jb‮c\nd") == "a\\u001b[2Jb\\u202ec\nd"


def test_f01_approval_is_bound_to_the_displayed_task(local_gateway, dashboard):
    from orrery_chatgpt_mcp import pairing

    config = local_gateway()
    p = tool(config, "orrery_spawn_preview", {"task": "shown task", "directory": "proj", "name": "BoundProbe"})["structuredContent"]
    code = p["approval"]["code"]
    pending = pairing.approvals_dir(config.state_dir) / f"pending-{code}.json"
    record = json.loads(pending.read_text())
    assert record["summary"]["task"] == "shown task" and record["summary"]["directory_path"].endswith("proj")
    record["summary"]["task"] = "different text"  # the operator is shown something other than the preview
    pending.write_text(json.dumps(record))
    pairing.approve(config.state_dir, code)
    r = tool(config, "orrery_spawn_commit", {"preview_id": p["preview_id"], "idempotency_key": "bound-0001"})
    assert r["structuredContent"]["error"]["code"] == "approval_required"
    assert dashboard.posts() == []


def test_model_is_pinned_to_the_previewed_default(local_gateway, dashboard):
    from orrery_chatgpt_mcp import pairing

    config = local_gateway()
    p = tool(config, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "PinProbe"})["structuredContent"]
    assert p["launch"]["model"] == "claude-opus-5-5"
    pairing.approve(config.state_dir, p["approval"]["code"])
    tool(config, "orrery_spawn_commit", {"preview_id": p["preview_id"], "idempotency_key": "pin-00001"})
    assert dashboard.posts()[0]["model"] == "claude-opus-5-5"


# -- F02 / F11 JWKS key handling ------------------------------------------------

class _FakeJwks:
    def __init__(self, keys):
        self.doc = {"keys": keys}
        self.fetches = 0

    def __call__(self):
        self.fetches += 1
        return json.dumps(self.doc).encode()


def _jwk(keys, **extra):
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(keys.private.public_key()))
    jwk.update({"kid": "k1", "alg": "RS256"}, **extra)
    jwk.update(extra)
    return jwk


def _verifier(keys, fetch, clock):
    from orrery_chatgpt_mcp.auth import TokenVerifier, _JwksSource
    from orrery_chatgpt_mcp.config import GatewayConfig

    cfg = GatewayConfig(auth_mode="oauth", resource=RESOURCE, issuer=ISSUER, allowed_subjects=frozenset({OPERATOR}),
                        jwks_url="https://auth.example.test/jwks.json").validate()
    return TokenVerifier(cfg, jwks_source=_JwksSource(cfg, clock=clock, fetch=fetch))


def test_f02_removed_signing_key_stops_verifying_after_refresh(keys):
    from orrery_chatgpt_mcp.auth import JWKS_TTL, AuthError

    now = [1000.0]
    fake = _FakeJwks([_jwk(keys)])
    verifier = _verifier(keys, fake, lambda: now[0])
    verifier.verify(keys.mint())
    fake.doc = {"keys": []}  # issuer removes the compromised key
    now[0] += JWKS_TTL + 1
    with pytest.raises(AuthError):
        verifier.verify(keys.mint())  # freshly minted with the removed key


def test_f02_unknown_kids_cannot_drive_unbounded_refreshes(keys):
    from orrery_chatgpt_mcp.auth import AuthError

    now = [1000.0]
    fake = _FakeJwks([_jwk(keys)])
    verifier = _verifier(keys, fake, lambda: now[0])
    for i in range(20):
        with pytest.raises(AuthError):
            verifier.verify(keys.mint(kid=f"nope{i}"))
    assert fake.fetches <= 2


def test_f11_encryption_keys_and_alg_mismatch_are_not_signing_keys(keys):
    from orrery_chatgpt_mcp.auth import AuthError

    with pytest.raises(AuthError):
        _verifier(keys, _FakeJwks([_jwk(keys, use="enc")]), lambda: 0.0).verify(keys.mint())
    with pytest.raises(AuthError):
        _verifier(keys, _FakeJwks([_jwk(keys, key_ops=["encrypt"])]), lambda: 0.0).verify(keys.mint())
    with pytest.raises(AuthError):
        _verifier(keys, _FakeJwks([_jwk(keys, alg="PS256")]), lambda: 0.0).verify(keys.mint())


# -- F03 framing ----------------------------------------------------------------

def test_f03_rejected_requests_close_and_never_run_a_smuggled_inner_request(local_gateway):
    config = local_gateway()
    host = f"127.0.0.1:{config.port}"
    inner = _req(config.mcp_path, json.dumps(rpc("ping")).encode(), host)
    statuses, closed = raw(config, _req(config.mcp_path, inner, host, b"Origin: https://evil.example\r\n"))
    assert statuses == [403] and closed
    statuses, closed = raw(config, _req("/mcp/wrong", inner, host))
    assert statuses == [404] and closed


def test_f03_ambiguous_framing_is_refused(local_gateway):
    config = local_gateway()
    host = f"127.0.0.1:{config.port}"
    body = json.dumps(rpc("ping")).encode()
    dup = (b"POST " + config.mcp_path.encode() + b" HTTP/1.1\r\nHost: " + host.encode() + b"\r\nContent-Type: application/json\r\n"
           b"Content-Length: " + str(len(body)).encode() + b"\r\nContent-Length: 5\r\n\r\n" + body)
    assert raw(config, dup)[0] == [400]
    te = _req(config.mcp_path, body, host, b"Transfer-Encoding: chunked\r\n")
    assert raw(config, te)[0] == [400]
    get_body = (b"GET /healthz HTTP/1.1\r\nHost: " + host.encode() + b"\r\nContent-Length: 4\r\n\r\nabcd")
    assert raw(config, get_body)[0] == [400]


# -- F04 rotation -----------------------------------------------------------------

def test_f04_rotation_revokes_old_url_in_running_gateway(local_gateway):
    from orrery_chatgpt_mcp import pairing

    config = local_gateway()
    old = config.mcp_path
    assert send(config, rpc("ping"), path=old)[0] == 200
    new = pairing.create_secret(config.state_dir, rotate=True)
    assert send(config, rpc("ping"), path=old)[0] == 404
    assert send(config, rpc("ping"), path=f"/mcp/{new}")[0] == 200
    pairing.secret_path(config.state_dir).unlink()
    assert send(config, rpc("ping"), path=f"/mcp/{new}")[0] == 404  # missing file fails closed


# -- F05 resource bounds --------------------------------------------------------------

def test_f05_oauth_authenticates_before_waiting_for_the_body(make_gateway):
    config, _ = make_gateway()
    host = f"127.0.0.1:{config.port}"
    head = (b"POST /mcp HTTP/1.1\r\nHost: " + host.encode() + b"\r\nContent-Type: application/json\r\nContent-Length: 65536\r\n\r\n")
    started = time.time()
    statuses, closed = raw(config, head, wait=3)
    assert statuses == [401] and closed and time.time() - started < 2.5


def test_f05_server_has_timeouts_and_a_concurrency_bound():
    from orrery_chatgpt_mcp import server

    assert server.SOCKET_TIMEOUT and server.SOCKET_TIMEOUT <= 30
    assert server.GatewayServer._slots._initial_value == server.MAX_CONCURRENT_REQUESTS


def test_single_instance_per_state_dir(tmp_path):
    from orrery_chatgpt_mcp.config import ConfigError, GatewayConfig
    from orrery_chatgpt_mcp.server import _instance_lock

    cfg = GatewayConfig(pairing_secret="A" * 43, state_dir=tmp_path)
    fd = _instance_lock(cfg)
    try:
        with pytest.raises(ConfigError, match="already running"):
            _instance_lock(cfg)
    finally:
        import os

        os.close(fd)


# -- F06 / F07 concurrent commits ---------------------------------------------------------

def _registry(config, dashboard_url):
    from orrery_chatgpt_mcp.dashboard import DashboardClient
    from orrery_chatgpt_mcp.tools import ToolRegistry

    return ToolRegistry(config, DashboardClient(dashboard_url))


def _principal():
    from orrery_chatgpt_mcp.auth import Principal

    return Principal(OPERATOR, frozenset({"orrery.read", "orrery.spawn"}), None, 0)


def test_f06_two_registries_sharing_state_never_double_dispatch(tmp_path, dashboard):
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.tools import ToolError

    proj = tmp_path / "p"
    proj.mkdir()
    cfg = GatewayConfig(pairing_secret="A" * 43, state_dir=tmp_path / "s", spawn_enabled=True, spawn_dirs={"proj": str(proj)},
                        spawn_approval="chat", dashboard_url=dashboard.url)
    regs = [_registry(cfg, dashboard.url) for _ in range(2)]
    previews = [r.call(_principal(), "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": n})["preview_id"]
                for r, n in zip(regs, ("ShareOne", "ShareTwo"))]
    barrier = threading.Barrier(2)
    results = []

    def go(i):
        barrier.wait()
        try:
            results.append(regs[i].call(_principal(), "orrery_spawn_commit", {"preview_id": previews[i], "idempotency_key": "shared-key"}))
        except ToolError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(dashboard.posts()) == 1
    assert "idempotency_conflict" in results


def test_f07_losing_concurrent_commit_keeps_its_own_preview(tmp_path, dashboard):
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.tools import ToolError

    proj = tmp_path / "p"
    proj.mkdir()
    cfg = GatewayConfig(pairing_secret="A" * 43, state_dir=tmp_path / "s", spawn_enabled=True, spawn_dirs={"proj": str(proj)},
                        spawn_approval="chat", dashboard_url=dashboard.url)
    reg = _registry(cfg, dashboard.url)
    a = reg.call(_principal(), "orrery_spawn_preview", {"task": "a", "directory": "proj", "name": "RaceAlpha"})["preview_id"]
    b = reg.call(_principal(), "orrery_spawn_preview", {"task": "b", "directory": "proj", "name": "RaceBeta"})["preview_id"]
    barrier = threading.Barrier(2)
    out = {}

    def go(label, pid):
        barrier.wait()
        try:
            out[label] = reg.call(_principal(), "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "race-key-1"})
        except ToolError as exc:
            out[label] = exc.code

    threads = [threading.Thread(target=go, args=x) for x in (("a", a), ("b", b))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(v if isinstance(v, str) else "ok" for v in out.values()) == ["idempotency_conflict", "ok"]
    loser = a if out["a"] == "idempotency_conflict" else b
    again = reg.call(_principal(), "orrery_spawn_commit", {"preview_id": loser, "idempotency_key": "race-key-2"})
    assert again["outcome"] == "accepted"
    assert len(dashboard.posts()) == 2


def test_ledger_that_is_valid_json_but_not_an_object_fails_closed(tmp_path):
    from orrery_chatgpt_mcp.tools import SpawnLedger, ToolError

    (tmp_path / "spawn-ledger.json").write_text("[]")
    with pytest.raises(ToolError):
        SpawnLedger(tmp_path).get("x")


# -- F08 partial send ---------------------------------------------------------------------

def test_f08_failure_while_sending_is_unknown_not_rejected(make_gateway, keys, dashboard, tmp_path, monkeypatch):
    config, _ = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(tmp_path)})
    token = keys.mint(scope=SPAWN)
    pid = call(config, token, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "PartialSend"})["structuredContent"]["preview_id"]
    original = http.client.HTTPConnection.request

    def request_then_fail(self, method, url, body=None, headers={}, **kw):
        original(self, method, url, body=body, headers=headers, **kw)
        if method == "POST" and url == "/api/spawn":
            raise BrokenPipeError("simulated")

    monkeypatch.setattr(http.client.HTTPConnection, "request", request_then_fail)
    r = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "partial-01"})
    assert r["structuredContent"]["error"]["code"] == "outcome_unknown"


def test_f08_connection_refused_is_definitely_not_sent(make_gateway, keys, dashboard, tmp_path):
    config, server = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(tmp_path)})
    token = keys.mint(scope=SPAWN)
    pid = call(config, token, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "RefusedSend"})["structuredContent"]["preview_id"]
    dashboard.close()
    r = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "refused-01"})
    assert r["structuredContent"]["error"]["code"] == "dashboard_unavailable"
    dashboard.server = type("S", (), {"shutdown": lambda s: None, "server_close": lambda s: None})()


# -- F09 / F10 / F12 ------------------------------------------------------------------------

def test_f09_dashboard_error_text_is_not_forwarded(make_gateway, keys, dashboard, tmp_path):
    config, _ = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(tmp_path)})
    token = keys.mint(scope=SPAWN)
    dashboard.spawn_status["LeakProbe"] = {"name": "LeakProbe", "state": "failed", "age": 1,
                                           "error": "spawn script missing: /Users/demo/private/secret.json value=SYNTHETIC", "result": {}}
    out = json.dumps(call(config, token, "orrery_spawn_status", {"name": "LeakProbe"}))
    assert "SYNTHETIC" not in out and "/Users/demo" not in out and '"has_error": true' in out
    dashboard.spawn_mode = "fail"
    pid = call(config, token, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "FailLeak"})["structuredContent"]["preview_id"]
    out = json.dumps(call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "leak-0001"}))
    assert "log tail" not in out and "launcher failed" not in out


def test_f10_unknown_tool_names_do_not_reach_the_log(make_gateway, keys, caplog):
    config, _ = make_gateway()
    with caplog.at_level(logging.INFO, logger="orrery_chatgpt_mcp"):
        call(config, keys.mint(), "missing\nFORGED_LOG_ENTRY")
    assert "FORGED_LOG_ENTRY" not in caplog.text


def test_f12_rejected_messages_get_http_errors(local_gateway):
    config = local_gateway()
    assert send(config, {"jsonrpc": "2.0", "id": 1, "result": {}})[0] == 400
    assert send(config, {"jsonrpc": "1.0", "id": 1, "method": "ping"})[0] == 400
    assert send(config, [rpc("ping")])[0] == 400
    assert send(config, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0] == 202


def test_generated_names_match_the_dashboard_rule(local_gateway, dashboard):
    import re

    config = local_gateway()
    dashboard_rule = re.compile(r"[A-Z][A-Za-z]{1,63}(?:-[A-Z][A-Za-z]{1,63})?")
    name = tool(config, "orrery_spawn_preview", {"task": "t", "directory": "proj"})["structuredContent"]["launch"]["name"]
    assert dashboard_rule.fullmatch(name)
    r = tool(config, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "Digit9Name"})
    assert r["structuredContent"]["error"]["code"] == "invalid_arguments"


# -- re-review RR01-RR04 and hardening ------------------------------------------------

def test_rr01_drip_fed_headers_hit_an_absolute_deadline(local_gateway, monkeypatch):
    from orrery_chatgpt_mcp import server

    monkeypatch.setattr(server, "REQUEST_DEADLINE", 1.0)
    config = local_gateway()
    s = socket.create_connection(("127.0.0.1", config.port), timeout=5)
    s.sendall(b"POST /mcp/x HTTP/1.1\r\nHost: 127.0.0.1\r\n")
    started = time.time()
    closed = False
    try:
        while time.time() - started < 4:
            time.sleep(0.3)
            s.sendall(b"X")  # keeps the socket active well inside SOCKET_TIMEOUT
    except OSError:
        closed = True
    else:
        s.settimeout(1)
        try:
            closed = s.recv(1024) == b""
        except OSError:
            closed = True
    finally:
        s.close()
    assert closed and time.time() - started < 4.5


def test_rr01_slow_dashboard_work_is_not_cut_by_the_receive_deadline(local_gateway, monkeypatch, dashboard):
    from orrery_chatgpt_mcp import server

    monkeypatch.setattr(server, "REQUEST_DEADLINE", 0.5)
    config = local_gateway()
    slow = threading.Event()
    real_agents = __import__("orrery_chatgpt_mcp.dashboard", fromlist=["DashboardClient"]).DashboardClient.agents

    def slow_agents(self):
        time.sleep(1.0)
        slow.set()
        return real_agents(self)

    monkeypatch.setattr("orrery_chatgpt_mcp.dashboard.DashboardClient.agents", slow_agents)
    r = tool(config, "orrery_list_agents")
    assert slow.is_set() and r["isError"] is False


def test_rr02_anonymous_failures_do_not_lock_out_a_valid_token(make_gateway, keys):
    config, _ = make_gateway()
    for _ in range(40):
        post(config, rpc("ping"))
    status, _, _ = post(config, rpc("ping"), token=keys.mint())
    assert status == 200


def test_rr03_failed_refreshes_respect_the_cooldown(keys):
    from orrery_chatgpt_mcp.auth import AuthError

    now = [1000.0]
    fake = _FakeJwks([_jwk(keys)])
    verifier = _verifier(keys, fake, lambda: now[0])
    verifier.verify(keys.mint())
    now[0] += 31

    def broken():
        fake.fetches += 1
        raise OSError("idp down")

    verifier._keys._fetch = broken
    for i in range(10):
        with pytest.raises(AuthError):
            verifier.verify(keys.mint(kid=f"unknown{i}"))
    assert fake.fetches == 2  # one success, one failed attempt; then cooling down
    now[0] += 300  # past the TTL with the IdP still down: fail closed, still bounded
    for _ in range(5):
        with pytest.raises(AuthError):
            verifier.verify(keys.mint())
    assert fake.fetches == 3


@pytest.mark.parametrize("doc", [{"keys": None}, {"keys": [{"kid": "k1", "key_ops": "verify-ish"}]}, [], {"nokeys": 1}])
def test_rr03_malformed_jwks_fail_cleanly(keys, doc):
    from orrery_chatgpt_mcp.auth import AuthError

    fake = _FakeJwks([])
    fake.doc = doc
    with pytest.raises(AuthError):
        _verifier(keys, fake, lambda: 0.0).verify(keys.mint())


def test_rr04_legacy_ledger_records_are_reprojected(tmp_path, dashboard):
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.tools import SpawnLedger, _digest

    proj = tmp_path / "p"
    proj.mkdir()
    state = tmp_path / "s"
    cfg = GatewayConfig(pairing_secret="A" * 43, state_dir=state, spawn_enabled=True, spawn_dirs={"proj": str(proj)},
                        spawn_approval="chat", dashboard_url=dashboard.url)
    reg = _registry(cfg, dashboard.url)
    pid = reg.call(_principal(), "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "LegacyRec"})["preview_id"]
    SpawnLedger(state).put_if_absent(SpawnLedger.key(OPERATOR, "legacy-001"), {
        "ts": time.time(), "preview_id": _digest(pid), "name": "LegacyRec", "state": "rejected",
        "result": {"error": "dir does not exist: /Users/demo/private SYNTHETIC_LEGACY_SECRET", "registration_retained": True, "http_status": 400}})
    out = reg.call(_principal(), "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "legacy-001"})
    assert "SYNTHETIC_LEGACY_SECRET" not in json.dumps(out) and out["result"]["registration_retained"] is True
    assert dashboard.posts() == []


def test_same_preview_simultaneous_retry_replays(tmp_path, dashboard):
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.tools import ToolError

    proj = tmp_path / "p"
    proj.mkdir()
    cfg = GatewayConfig(pairing_secret="A" * 43, state_dir=tmp_path / "s", spawn_enabled=True, spawn_dirs={"proj": str(proj)},
                        spawn_approval="chat", dashboard_url=dashboard.url)
    reg = _registry(cfg, dashboard.url)
    pid = reg.call(_principal(), "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "RetryTwin"})["preview_id"]
    barrier = threading.Barrier(2)
    out = []

    def go():
        barrier.wait()
        try:
            out.append(reg.call(_principal(), "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "twin-key-1"}))
        except ToolError as exc:
            out.append(exc.code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(isinstance(o, dict) for o in out), out
    assert len(dashboard.posts()) == 1


def test_dry_run_without_a_model_is_refused(local_gateway, dashboard, monkeypatch):
    from orrery_chatgpt_mcp.dashboard import DashboardClient

    config = local_gateway()
    real = DashboardClient.spawn

    def no_model(self, payload):
        status, body = real(self, payload)
        body.pop("model", None)
        return status, body

    monkeypatch.setattr(DashboardClient, "spawn", no_model)
    r = tool(config, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "NoModel"})
    assert r["structuredContent"]["error"]["code"] == "preview_rejected"


def test_approve_refuses_a_record_changed_after_display(local_gateway):
    from orrery_chatgpt_mcp import pairing
    from orrery_chatgpt_mcp.config import ConfigError

    config = local_gateway()
    code = tool(config, "orrery_spawn_preview", {"task": "t", "directory": "proj", "name": "ShownOnce"})["structuredContent"]["approval"]["code"]
    shown = pairing.read_pending(config.state_dir, code)
    changed = json.loads(json.dumps(shown))
    changed["summary"]["task"] = "swapped"
    with pytest.raises(ConfigError, match="changed"):
        pairing.approve(config.state_dir, code, shown=changed)


def test_pairing_permission_change_fails_closed_in_running_gateway(local_gateway):
    from orrery_chatgpt_mcp import pairing

    config = local_gateway()
    assert send(config, rpc("ping"))[0] == 200
    pairing.secret_path(config.state_dir).chmod(0o644)
    assert send(config, rpc("ping"))[0] == 404
