"""Idle watch: transitions, ask suppression, rearm, no double fire, fail-closed."""
import json
import math

import pytest

from dashboard import idle_watch as iw
from dashboard import server

P = "/proj"
CFG = {P: {**iw.DEFAULTS, "parent": "Parent", "samples": 3, "seconds": 25,
           "resend_after": 100, "max_resends": 2, "resend_cap": 1000}}


def rows(**states):
    return [dict(name=n, running=True, project_key=P, act_state=s)
            for n, s in states.items()]


class Rig:
    def __init__(self, status="sent"):
        self.t, self.wakes, self.asks, self.status = 1000.0, [], [], status

        def wake(*a):
            self.wakes.append(a)
            if isinstance(self.status, Exception):
                raise self.status
            return self.status
        self.w = iw.IdleWatch(CFG, wake, lambda *a: self.asks.append(a))

    def run(self, r, n=1, step=10, fresh=True):
        for _ in range(n):
            self.t += step
            self.w.tick(r, self.t if fresh else self.t - 999, self.t)


def test_work_then_wait_fires_once_with_evidence():
    g = Rig()
    g.run(rows(Parent="work", Kid="work"), 2)
    g.run(rows(Parent="wait", Kid="wait"), 2)
    assert not g.wakes                      # N=3 not met yet
    g.run(rows(Parent="wait", Kid="wait"), 1)
    assert not g.wakes                      # N met but T=25 s not (20 s elapsed)
    g.run(rows(Parent="wait", Kid="wait"), 1)
    assert len(g.wakes) == 1
    _, _, ev, attempt = g.wakes[0]
    assert attempt == 1
    assert ev["Kid"]["state"] == "wait" and ev["Kid"]["seconds"] >= 30
    g.run(rows(Parent="wait", Kid="wait"), 5)
    assert len(g.wakes) == 1                # same idle interval: no double fire


def test_ask_suppresses_resume_and_notifies_once():
    g = Rig()
    g.run(rows(Parent="wait", Kid="ask"), 10)
    assert not g.wakes and len(g.asks) == 1


def test_work_return_clears_interval_and_rearms():
    g = Rig()
    g.run(rows(Parent="wait", Kid="wait"), 4)
    assert len(g.wakes) == 1
    g.run(rows(Parent="wait", Kid="work"), 1)
    g.run(rows(Parent="wait", Kid="wait"), 4)
    assert len(g.wakes) == 2


def test_unknown_or_stale_is_not_idle():
    g = Rig()
    g.run(rows(Parent="wait", Kid=None), 10)
    g.run(rows(Parent="wait", Kid="wait"), 10, fresh=False)
    g.run(None, 10)
    assert not g.wakes


def test_idle_streak_resets_on_unknown_sample():
    g = Rig()
    g.run(rows(Parent="wait", Kid="wait"), 2)
    g.run(rows(Parent="wait", Kid=None), 1)
    g.run(rows(Parent="wait", Kid="wait"), 2)
    assert not g.wakes


def test_resend_backoff_is_capped_by_count():
    g = Rig()
    r = rows(Parent="wait", Kid="wait")
    g.run(r, 4)                             # fires at t+40
    assert len(g.wakes) == 1
    g.run(r, 9)                             # +90 s < 100 s
    assert len(g.wakes) == 1
    g.run(r, 1)
    assert len(g.wakes) == 2                # resend 1 after 100 s
    g.run(r, 19)                            # +190 s < 200 s
    assert len(g.wakes) == 2
    g.run(r, 1)
    assert len(g.wakes) == 3                # resend 2 after 200 s
    g.run(r, 500)
    assert len(g.wakes) == 3                # max_resends reached


def test_absent_parent_is_never_resumed_or_mailed():
    g = Rig()
    g.run(rows(Kid="wait"), 10)
    assert not g.wakes


def test_other_project_agents_are_ignored():
    g = Rig()
    r = rows(Parent="wait", Kid="wait") + [dict(name="X", running=True, project_key="/other", act_state="work")]
    g.run(r, 4)
    assert len(g.wakes) == 1


def test_config_is_opt_in(tmp_path):
    f = tmp_path / "c.json"
    assert iw.load_config(f) == {}
    f.write_text(json.dumps({"projects": {P: {"parent": "Parent"}, "/b": {"enabled": True, "parent": "B", "seconds": 5}}}))
    cfg = iw.load_config(f)
    assert list(cfg) == ["/b"] and cfg["/b"]["seconds"] == 5 and cfg["/b"]["samples"] == 3


def test_evidence_carries_last_work_for_sender_choice():
    g = Rig()
    g.run(rows(Parent="work", A="work", B="wait"), 1)
    g.run(rows(Parent="wait", A="wait", B="work"), 1)
    g.run(rows(Parent="wait", A="wait", B="wait"), 4)
    ev = g.wakes[0][2]
    assert ev["B"]["last_work"] > ev["A"]["last_work"] > 0


# --- fail-closed membership (PR50 audit A/B) ---

@pytest.mark.parametrize("key", ["/other", None])
def test_parent_row_outside_this_project_is_unknown(key):
    # None: the parent name is unregistered or registered in several projects.
    r = [dict(name="Parent", running=True, project_key=key, mail_linked=True, act_state="wait"),
         dict(name="Kid", running=True, project_key=P, act_state="wait")]
    assert iw.classify(r, P, "Parent")[0] == "unknown"
    g = Rig()
    g.run(r, 10)
    assert not g.wakes


@pytest.mark.parametrize("state", ["work", "ask", None])
def test_mail_linked_row_of_unknown_project_blocks_unless_waiting(state):
    # A name registered in two projects shows project_key=None; it may be ours.
    amb = dict(name="Twin", running=True, project_key=None, mail_linked=True, act_state=state)
    g = Rig()
    g.run(rows(Parent="wait", Kid="wait") + [amb], 10)
    assert not g.wakes


def test_unlinked_or_waiting_unknown_project_rows_do_not_block():
    extra = [dict(name="shell", running=True, project_key=None, mail_linked=False, act_state=None),
             dict(name="Twin", running=True, project_key=None, mail_linked=True, act_state="wait")]
    g = Rig()
    g.run(rows(Parent="wait", Kid="wait") + extra, 4)
    assert len(g.wakes) == 1 and "Twin" not in g.wakes[0][2]


@pytest.mark.parametrize("bad", [dict(running=False), dict(retired_but_alive=True)])
def test_member_with_unknown_liveness_or_retired_is_unknown(bad):
    r = rows(Parent="wait", Kid="wait")
    r[1].update(bad)
    g = Rig()
    g.run(r, 10)
    assert not g.wakes


# --- send budget and delivery status (PR50 audit C) ---

@pytest.mark.parametrize("gap", ["none", "stale", "missing", "ask"])
def test_unknown_or_blocked_gap_does_not_reset_send_budget(gap):
    g = Rig()
    r = rows(Parent="wait", Kid="wait")
    for _ in range(8):
        g.run(r, 4)
        if gap == "none":
            g.run(None, 1)
        elif gap == "stale":
            g.run(r, 1, fresh=False)
        elif gap == "missing":
            g.run(rows(Parent="wait", Kid=None), 1)
        else:
            g.run(rows(Parent="wait", Kid="ask"), 1)
    # 8 * 50 s = 400 s: initial at 40 s, resend 1 at >=140 s, resend 2 at >=340 s.
    assert [a[3] for a in g.wakes] == [1, 2, 3]
    g.run(r, 400)
    assert len(g.wakes) == 3                # max_resends=2 holds across gaps
    g.run(rows(Parent="wait", Kid="work"), 1)
    g.run(r, 4)
    assert len(g.wakes) == 4 and g.wakes[-1][3] == 1  # real work re-arms


@pytest.mark.parametrize("status", ["delivery_unknown", "rejected", "no_sender",
                                    "state_changed", RuntimeError("boom")])
def test_unconfirmed_delivery_is_never_resent(status):
    g = Rig(status=status)
    r = rows(Parent="wait", Kid="wait")
    with pytest.raises(RuntimeError) if isinstance(status, Exception) else _nullctx():
        g.run(r, 4)
    g.run(r, 500)
    assert len(g.wakes) == 1


class _nullctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("ts_offset", [+60, math.nan, math.inf])
def test_future_or_nonfinite_timestamp_is_not_fresh(ts_offset):
    g = Rig()
    for _ in range(10):
        g.t += 10
        g.w.tick(rows(Parent="wait", Kid="wait"), g.t + ts_offset, g.t)
    assert not g.wakes


# --- configuration (PR50 audit D) ---

@pytest.mark.parametrize("raw", ['{"projects": []}', '{"projects": null}', '[]', '"x"', '{bad'])
def test_malformed_config_disables_without_raising(tmp_path, raw):
    f = tmp_path / "c.json"
    f.write_text(raw)
    assert iw.load_config(f) == {}


@pytest.mark.parametrize("key,value", [("poll", 0), ("poll", -1), ("poll", False), ("poll", "10"),
                                       ("poll", None), ("poll", 1.5), ("stale", "NaN"),
                                       ("seconds", -5), ("samples", 0), ("max_resends", 11),
                                       ("resend_after", "Infinity"), ("resend_cap", "1e309")])
def test_invalid_setting_disables_only_that_project(tmp_path, key, value):
    f = tmp_path / "c.json"
    literal = value if isinstance(value, str) and value in ("NaN", "Infinity", "1e309") else json.dumps(value)
    f.write_text('{"projects": {"/a": {"enabled": true, "parent": "A", "%s": %s},'
                 ' "/b": {"enabled": true, "parent": "B"}}}' % (key, literal))
    cfg = iw.load_config(f)
    assert list(cfg) == ["/b"] and cfg["/b"]["poll"] == 10


# --- server wiring: no resume, re-check before send, delivery status ---

def _server_rig(monkeypatch, live_rows, mcp_result):
    sent = []
    monkeypatch.setattr(server, "_idle_watch_sender", lambda *a: ("Kid", "tok"))
    monkeypatch.setattr(server, "_mcp_tool_parameters", lambda tool: {"to"})
    monkeypatch.setattr(server.idle_watch, "fetch_rows", lambda port: (live_rows, 1000.0))
    monkeypatch.setattr(server.time, "time", lambda: 1000.0)
    monkeypatch.setattr(server, "_mcp_call", lambda m, a, timeout=15: sent.append(a) or mcp_result)
    monkeypatch.setattr(server, "do_jump", lambda *a, **k: pytest.fail("idle-watch must not resume"))
    return sent


EV = {"Parent": dict(state="wait", seconds=60, last_work=None),
      "Kid": dict(state="wait", seconds=60, last_work=None)}


@pytest.mark.parametrize("kid", ["ask", "work", None])
def test_server_wake_rechecks_state_before_sending(monkeypatch, kid):
    sent = _server_rig(monkeypatch, rows(Parent="wait", Kid=kid), {"ok": True})
    assert server._idle_watch_wake(P, CFG[P], EV, 1) == "state_changed" and not sent


def test_server_wake_reports_delivery_unknown_and_labels_watcher(monkeypatch):
    sent = _server_rig(monkeypatch, rows(Parent="wait", Kid="wait"), {"ok": False, "error": "TimeoutError"})
    assert server._idle_watch_wake(P, CFG[P], EV, 1) == "delivery_unknown"
    assert len(sent) == 1 and "not by Kid" in sent[0]["body_md"] and sent[0]["to"] == ["Parent"]


def test_server_wake_sent_and_rejected(monkeypatch):
    _server_rig(monkeypatch, rows(Parent="wait", Kid="wait"), {"ok": True})
    assert server._idle_watch_wake(P, CFG[P], EV, 1) == "sent"
    _server_rig(monkeypatch, rows(Parent="wait", Kid="wait"), {"ok": False, "rejected": True})
    assert server._idle_watch_wake(P, CFG[P], EV, 1) == "rejected"


@pytest.mark.parametrize("mode", ["GLOBAL_MODE", "FRESH_MODE"])
def test_watch_does_not_start_outside_legacy_mode(monkeypatch, tmp_path, mode):
    (tmp_path / "idle-watch.json").write_text(json.dumps({"projects": {P: {"enabled": True, "parent": "Parent"}}}))
    monkeypatch.setattr(server, "RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(server, mode, True)
    monkeypatch.setattr(server.threading, "Thread", lambda *a, **k: pytest.fail("thread started"))
    server._start_idle_watch()


def test_server_wake_stops_when_send_schema_is_unavailable(monkeypatch):
    sent = _server_rig(monkeypatch, rows(Parent="wait", Kid="wait"), {"ok": True})
    monkeypatch.setattr(server, "_mcp_tool_parameters", lambda tool: None)
    assert server._idle_watch_wake(P, CFG[P], EV, 1) == "schema_unavailable" and not sent
