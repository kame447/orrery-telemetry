"""Messages to existing agents: contract tests with a fake sender (no Mail process)."""
from __future__ import annotations

import contextlib
import json
import stat
import threading

import pytest

import chatgpt_gateway_support  # noqa: F401  (puts the gateway source on sys.path)
from orrery_chatgpt_mcp import pairing
from orrery_chatgpt_mcp.auth import Principal
from orrery_chatgpt_mcp.config import APPROVAL_LOCAL, ConfigError, GatewayConfig, SCOPE_MESSAGE, SCOPE_READ
from orrery_chatgpt_mcp.dashboard import DashboardError
from orrery_chatgpt_mcp.messaging import MessageTools, SenderError, unsafe_text
from orrery_chatgpt_mcp.tools import ToolError, ToolRegistry

ME = Principal("local-operator", frozenset({SCOPE_READ, SCOPE_MESSAGE}), None, 0)
INSTANCE = "8a4c1f43-6a43-4f2e-9a39-0f6c2b0a7d11"
WORKER = {"name": "ExampleWorker", "id": 7, "program": "claude-code", "project_id": 3, "registered_at": "2026-10-29T00:00:00", "retired": False}


class FakeSession:
    def __init__(self, sender):
        self.s = sender

    def identity(self):
        return dict(self.s.identity)

    def lookup(self, name):
        if self.s.lookup_error:
            raise SenderError(self.s.lookup_error, sent=False)
        return dict(self.s.peers[name])

    def send(self, **kw):
        self.s.sent.append(kw)
        if self.s.send_error:
            code, sent = self.s.send_error
            raise SenderError(code, sent=sent)
        return {"deliveries": [{"project": "p", "payload": {"id": 100 + len(self.s.sent)}}], "count": 1}


class FakeSender:
    def __init__(self):
        self.identity = {"name": "ChatRelay", "agent_id": 9, "project_key": "/Users/x/proj", "instance": INSTANCE}
        self.peers = {"ExampleWorker": dict(WORKER)}
        self.pinned = {"ExampleWorker": 7}
        self.sent: list[dict] = []
        self.send_error = None
        self.lookup_error = None
        self.unavailable = None

    def allowed(self):
        if self.unavailable:
            raise SenderError(self.unavailable, sent=False)
        return dict(self.pinned)

    @contextlib.contextmanager
    def session(self):
        if self.unavailable:
            raise SenderError(self.unavailable, sent=False)
        yield FakeSession(self)


class StubDashboard:
    def __init__(self):
        self.rows: list[dict] = []
        self.down = False

    def edge_messages(self, a, b, limit=200):
        if self.down:
            raise DashboardError("down")
        return list(self.rows)


@pytest.fixture
def env(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    def make(**overrides):
        cfg = dict(state_dir=state, message_enabled=True, message_profile=tmp_path / "daemon.json", message_helper_root=tmp_path)
        cfg.update(overrides)
        config = GatewayConfig(pairing_secret="A" * 43, **cfg)
        return config

    sender, dash = FakeSender(), StubDashboard()

    def tools(config=None, clock=None):
        import time

        return MessageTools(config or make(), dash, clock=clock or time.time, sender=sender)

    return {"make": make, "tools": tools, "sender": sender, "dash": dash, "state": state}


def preview(t, **args):
    args.setdefault("recipient", "ExampleWorker")
    args.setdefault("instruction", "テストを追加してから PR を更新してください。")
    return t.preview(ME, args)


def commit(t, p, key="key-00000001", digest=None):
    return t.commit(ME, {"preview_id": p["preview_id"], "idempotency_key": key, "content_sha256": digest or p["content_sha256"]})


# -- off by default -------------------------------------------------------------

def test_messages_are_off_by_default_and_need_explicit_profile(tmp_path):
    base = dict(pairing_secret="A" * 43, state_dir=tmp_path)
    config = GatewayConfig(**base).validate()
    assert config.message_enabled is False
    with pytest.raises(ConfigError, match="ORRERY_MCP_MESSAGE_PROFILE"):
        GatewayConfig(**base, message_enabled=True, message_helper_root=tmp_path).validate()
    with pytest.raises(ConfigError, match="ambiguous"):
        GatewayConfig(**base, message_profile=tmp_path / "p.json").validate()
    with pytest.raises(ConfigError, match="chat or local"):
        GatewayConfig(**base, message_approval="none").validate()


def test_from_env_keeps_messages_off_without_the_switch(tmp_path):
    state = tmp_path / "state"
    pairing.create_secret(state)
    config = GatewayConfig.from_env({"ORRERY_MCP_STATE_DIR": str(state)})
    assert config.message_enabled is False and config.message_profile is None
    with pytest.raises(ConfigError, match="0 or 1"):
        GatewayConfig.from_env({"ORRERY_MCP_STATE_DIR": str(state), "ORRERY_MCP_ENABLE_MESSAGE": "yes"})


def test_registry_without_messages_has_no_message_tools(dashboard, tmp_path):
    from orrery_chatgpt_mcp.dashboard import DashboardClient

    config = GatewayConfig(pairing_secret="A" * 43, state_dir=tmp_path, dashboard_url=dashboard.url)
    registry = ToolRegistry(config, DashboardClient(dashboard.url))
    names = {t["name"] for t in registry.definitions()}
    assert not any(n.startswith("orrery_message") for n in names)
    caps = registry.call(Principal("local-operator", frozenset({SCOPE_READ}), None, 0), "orrery_status", {})["capabilities"]
    assert caps["message_existing_agent"] is False and caps["message_recipients"] == []
    with pytest.raises(ToolError) as exc:
        registry.call(ME, "orrery_message_commit", {})
    assert exc.value.code == "unknown_tool"


def test_registry_with_messages_lists_tools_and_recipients(env, dashboard):
    from orrery_chatgpt_mcp.dashboard import DashboardClient

    config = env["make"](dashboard_url=dashboard.url)
    registry = ToolRegistry(config, DashboardClient(dashboard.url), message_tools=env["tools"](config))
    defs = {t["name"]: t for t in registry.definitions()}
    assert {"orrery_message_preview", "orrery_message_commit", "orrery_message_status"} <= set(defs)
    assert defs["orrery_message_commit"]["annotations"]["destructiveHint"] is True
    assert "ExampleWorker" in defs["orrery_message_preview"]["description"]
    caps = registry.call(ME, "orrery_status", {})["capabilities"]
    assert caps["message_existing_agent"] is True and caps["message_recipients"] == ["ExampleWorker"]
    # Read scope alone cannot preview or send.
    with pytest.raises(ToolError) as exc:
        registry.call(Principal("x", frozenset({SCOPE_READ}), None, 0), "orrery_message_preview", {})
    assert exc.value.code == "insufficient_scope"


# -- preview -----------------------------------------------------------------------

def test_preview_shows_exact_message_and_sends_nothing(env):
    t = env["tools"]()
    p = preview(t, subject="追加指示")
    msg = p["message"]
    assert msg["to"] == {"name": "ExampleWorker", "mail_id": 7, "program": "claude-code", "project_id": 3}
    assert msg["from"] == {"name": "ChatRelay", "mail_id": 9}
    assert msg["mail_instance"] == INSTANCE and msg["project"] == "proj"
    assert msg["subject"] == "[ChatGPT] 追加指示"
    assert msg["body"].startswith("テストを追加してから PR を更新してください。\n\n---\n")
    assert "orrery-mcp-ref:" in msg["body"] and len(p["content_sha256"]) == 64
    assert env["sender"].sent == []


@pytest.mark.parametrize("text", [
    "ok‮evil", "ok⁦x⁩", "zero​width", "bom﻿", "c1\x85", "bell\x07", "tag\U000e0041",
    "line sep", "private", "nul\x00",
])
def test_hidden_or_control_characters_are_refused(env, text):
    with pytest.raises(ToolError) as exc:
        preview(env["tools"](), instruction=text)
    assert exc.value.code == "invalid_arguments"
    assert env["sender"].sent == []


def test_subject_must_be_one_visible_line(env):
    for subject in ("two\nlines", "tab\there", "x" * 121, " "):
        with pytest.raises(ToolError):
            preview(env["tools"](), subject=subject)
    assert not unsafe_text("改行\nとタブ\tは本文なら可", multiline=True)


def test_unlisted_recipient_and_unexpected_arguments_are_refused(env):
    t = env["tools"]()
    with pytest.raises(ToolError) as exc:
        preview(t, recipient="SomeoneElse")
    assert exc.value.code == "recipient_not_allowed" and exc.value.data["allowed"] == ["ExampleWorker"]
    with pytest.raises(ToolError) as exc:
        preview(t, cc=["Other"])
    assert exc.value.code == "invalid_arguments"
    with pytest.raises(ToolError):
        preview(t, recipient="Bad-Name")


@pytest.mark.parametrize("change", ["id", "retired", "missing"])
def test_preview_refuses_a_recipient_whose_identity_changed(env, change):
    s = env["sender"]
    if change == "id":
        s.peers["ExampleWorker"]["id"] = 8  # name recycled to a new Mail row
    elif change == "retired":
        s.peers["ExampleWorker"]["retired"] = True
    else:
        s.lookup_error = "daemon-peer-missing"
    with pytest.raises(ToolError) as exc:
        preview(env["tools"]())
    assert exc.value.code == "recipient_changed"


def test_unusable_sender_fails_closed(env):
    env["sender"].unavailable = "daemon-binding-mismatch"
    with pytest.raises(ToolError) as exc:
        preview(env["tools"]())
    assert exc.value.code == "sender_unavailable" and exc.value.data["reason"] == "daemon-binding-mismatch"


# -- commit / idempotency ----------------------------------------------------------

def test_commit_sends_once_and_replays(env):
    t = env["tools"]()
    p = preview(t)
    out = commit(t, p)
    assert out["outcome"] == "accepted" and out["message"]["mail_message_id"] == 101
    sent = env["sender"].sent
    assert len(sent) == 1 and sent[0]["to"] == "ExampleWorker" and sent[0]["body_md"] == p["message"]["body"]
    again = commit(t, p)
    assert again["replayed"] is True and again["outcome"] == "accepted"
    assert len(sent) == 1


def test_commit_needs_the_preview_digest(env):
    t = env["tools"]()
    p = preview(t)
    with pytest.raises(ToolError) as exc:
        commit(t, p, digest="0" * 64)
    assert exc.value.code == "content_mismatch"
    assert env["sender"].sent == []
    assert commit(t, p)["outcome"] == "accepted"  # the real digest still works once


def test_key_reuse_for_another_message_and_preview_reuse_are_refused(env):
    t = env["tools"]()
    p1, p2 = preview(t), preview(t, instruction="別の指示")
    commit(t, p1)
    with pytest.raises(ToolError) as exc:
        commit(t, p2)
    assert exc.value.code == "idempotency_conflict"
    with pytest.raises(ToolError) as exc:
        commit(t, p1, key="key-00000002")
    assert exc.value.code == "preview_consumed"
    assert len(env["sender"].sent) == 1


@pytest.mark.parametrize("field,value", [("id", 8), ("program", "codex"), ("project_id", 4), ("retired", True),
                                         ("registered_at", "2026-10-30T00:00:00")])
def test_recipient_change_after_preview_is_not_sent(env, field, value):
    t = env["tools"]()
    p = preview(t)
    env["sender"].peers["ExampleWorker"][field] = value
    with pytest.raises(ToolError) as exc:
        commit(t, p)
    assert exc.value.code == "recipient_changed"
    assert env["sender"].sent == []
    status = t.status(ME, {"idempotency_key": "key-00000001"})
    assert status["message"]["state"] == "rejected" and status["message"]["reason"] == "recipient-changed"


def test_sender_change_after_preview_is_not_sent(env):
    t = env["tools"]()
    p = preview(t)
    env["sender"].identity["instance"] = "00000000-0000-4000-8000-000000000000"
    with pytest.raises(ToolError) as exc:
        commit(t, p)
    assert exc.value.code == "not_sent" and env["sender"].sent == []


def test_pre_send_daemon_refusal_is_reported_as_not_sent(env):
    t = env["tools"]()
    p = preview(t)
    env["sender"].send_error = ("daemon-peer-changed", False)
    with pytest.raises(ToolError) as exc:
        commit(t, p)
    assert exc.value.code == "recipient_changed"
    assert t.status(ME, {"idempotency_key": "key-00000001"})["message"]["state"] == "rejected"


def test_unknown_outcome_is_never_resent_and_reconciles_from_mail(env):
    t = env["tools"]()
    p = preview(t)
    env["sender"].send_error = ("daemon-operation-failed", None)
    with pytest.raises(ToolError) as exc:
        commit(t, p)
    assert exc.value.code == "outcome_unknown"
    assert len(env["sender"].sent) == 1
    env["sender"].send_error = None
    # Not visible in Mail yet: still unknown, and a repeated commit does not send again.
    again = commit(t, p)
    assert again["outcome"] == "unknown" and again["observation"]["mail_record"] == "not_found"
    env["dash"].down = True
    assert t.status(ME, {"idempotency_key": "key-00000001"})["observation"]["mail_record"] == "unavailable"
    env["dash"].down = False
    ref = p["message"]["body"].rsplit("orrery-mcp-ref: ", 1)[1]
    env["dash"].rows = [
        {"id": 55, "sender": "Imposter", "recipient": "ExampleWorker", "body": f"orrery-mcp-ref: {ref}"},
        {"id": 56, "sender": "ChatRelay", "recipient": "ExampleWorker", "body": f"x\norrery-mcp-ref: {ref}", "read_ts": "t", "ack_ts": None},
    ]
    status = t.status(ME, {"idempotency_key": "key-00000001"})
    assert status["message"]["state"] == "accepted" and status["message"]["reconciled"] is True
    assert status["message"]["mail_message_id"] == 56
    assert status["observation"] == {"mail_record": "found", "read": True, "acknowledged": False}
    assert "not observable" in status["note"]
    assert commit(t, p)["outcome"] == "accepted"
    assert len(env["sender"].sent) == 1


def test_status_reports_read_and_ack_by_message_id(env):
    t = env["tools"]()
    p = preview(t)
    commit(t, p)
    env["dash"].rows = [{"id": 101, "sender": "ChatRelay", "recipient": "ExampleWorker", "read_ts": "t", "ack_ts": "t2"}]
    out = t.status(ME, {"idempotency_key": "key-00000001"})
    assert out["observation"] == {"mail_record": "found", "read": True, "acknowledged": True}
    with pytest.raises(ToolError) as exc:
        t.status(ME, {"idempotency_key": "never-used-key"})
    assert exc.value.code == "not_found"
    # Another connection subject cannot read this record.
    with pytest.raises(ToolError):
        t.status(Principal("other", ME.scopes, None, 0), {"idempotency_key": "key-00000001"})


def test_hourly_cap_counts_durable_records(env):
    config = env["make"](message_max_per_hour=2)
    t = env["tools"](config)
    for i in range(2):
        commit(t, preview(t), key=f"key-cap-{i:04d}")
    with pytest.raises(ToolError) as exc:
        commit(t, preview(t), key="key-cap-9999")
    assert exc.value.code == "rate_limited"
    assert len(env["sender"].sent) == 2


def test_ledger_survives_restart_but_previews_do_not(env):
    t1 = env["tools"]()
    p = preview(t1)
    commit(t1, p)
    t2 = env["tools"]()  # a new process with the same state dir
    assert commit(t2, p)["replayed"] is True
    p2 = preview(t1, instruction="second")
    with pytest.raises(ToolError) as exc:
        commit(t2, p2, key="key-00000002")
    assert exc.value.code == "preview_not_found"
    assert len(env["sender"].sent) == 1


def test_racing_commits_with_one_key_send_once(env):
    t = env["tools"]()
    p = preview(t)
    results, errors = [], []

    def go():
        try:
            results.append(commit(t, p))
        except ToolError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(env["sender"].sent) == 1
    assert len(results) == 8 and not errors


def test_local_approval_mode_binds_the_exact_body(env):
    config = env["make"](message_approval=APPROVAL_LOCAL)
    t = env["tools"](config)
    p = preview(t)
    code = p["approval"]["code"]
    with pytest.raises(ToolError) as exc:
        commit(t, p)
    assert exc.value.code == "approval_required" and env["sender"].sent == []
    record = pairing.read_pending(env["state"], code)
    assert record["summary"]["kind"] == "message" and record["summary"]["task"] == p["message"]["body"]
    pairing.approve(env["state"], code, shown=record)
    # An approval that does not cover this body is refused.
    approved = env["state"] / "approvals" / f"approved-{code}.json"
    good = approved.read_text()
    approved.write_text(json.dumps({**json.loads(good), "task_sha256": "0" * 64}))
    with pytest.raises(ToolError):
        commit(t, p)
    approved.write_text(good)
    assert commit(t, p)["outcome"] == "accepted"


def test_audit_and_ledger_hold_no_body_and_are_private(env):
    t = env["tools"]()
    p = preview(t, instruction="SECRET-BODY-TEXT do this")
    commit(t, p)
    for name in ("message-audit.jsonl", "message-ledger.json"):
        path = env["state"] / name
        assert "SECRET-BODY-TEXT" not in path.read_text()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    events = [json.loads(line)["event"] for line in (env["state"] / "message-audit.jsonl").read_text().splitlines()]
    assert events == ["preview", "dispatch", "accepted"]


def test_cli_describes_a_pending_message_with_escapes(env):
    from orrery_chatgpt_mcp.server import _describe

    config = env["make"](message_approval=APPROVAL_LOCAL)
    p = preview(env["tools"](config))
    text = _describe(pairing.read_pending(env["state"], p["approval"]["code"]), full=True)
    assert "message to ExampleWorker (Mail ID 7) from ChatRelay" in text
    assert "<end of message>" in text and "テストを追加" in text
