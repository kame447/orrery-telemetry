"""Idle watch: transitions, ask suppression, rearm and no double fire."""
import json

from dashboard import idle_watch as iw

P = "/proj"
CFG = {P: {**iw.DEFAULTS, "parent": "Parent", "samples": 3, "seconds": 25,
           "resend_after": 100, "max_resends": 2, "resend_cap": 1000}}


def rows(**states):
    return [dict(name=n, running=True, project_key=None if n == "Parent" else P, act_state=s)
            for n, s in states.items()]


class Rig:
    def __init__(self):
        self.t, self.wakes, self.asks = 1000.0, [], []
        self.w = iw.IdleWatch(CFG, lambda *a: self.wakes.append(a), lambda *a: self.asks.append(a))

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
    _, _, parent_running, ev, attempt = g.wakes[0]
    assert parent_running and attempt == 1
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


def test_gone_parent_is_resumed():
    g = Rig()
    g.run(rows(Kid="wait"), 4)
    assert len(g.wakes) == 1 and g.wakes[0][2] is False


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
