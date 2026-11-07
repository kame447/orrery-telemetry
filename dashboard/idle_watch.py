"""Project idle watch: wake a parent once when no agent of a project is working.

Pure decision logic. The caller supplies /api/agents rows and the delivery
callbacks; nothing here types into a pane, reads credentials or touches Mail.
"""
from __future__ import annotations

import json
import logging
import urllib.request

DEFAULTS = dict(samples=3, seconds=60, poll=10, stale=30,
                resend_after=600, max_resends=3, resend_cap=3600)


def load_config(path):
    """Return {project_key: settings} for enabled projects; absent file = none."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle).get("projects", {})
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for key, value in raw.items():
        if (isinstance(value, dict) and value.get("enabled") is True
                and isinstance(value.get("parent"), str) and value["parent"]):
            out[key] = {**DEFAULTS,
                        **{k: v for k, v in value.items() if k in DEFAULTS or k in ("parent", "sender")}}
    return out


def fetch_rows(port, timeout=20):
    url = f"http://127.0.0.1:{port}/api/agents?scope=live"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read())
    return payload["agents"], float(payload["ts"])


def classify(rows, project, parent):
    """Return (class, states). class: busy | idle | blocked | unknown.

    Members are running agents of the project plus the parent (whose own
    project_key may be unset). An act_state other than work/wait/ask, or no
    member at all, is unknown and never counts as idle.
    """
    members = [r for r in rows if r.get("running")
               and (r.get("project_key") == project or r.get("name") == parent)]
    states = {r["name"]: r.get("act_state") for r in members}
    if "work" in states.values():
        return "busy", states
    if not states or any(s not in ("wait", "ask") for s in states.values()):
        return "unknown", states
    return ("blocked" if "ask" in states.values() else "idle"), states


class IdleWatch:
    """One state machine per project; tick() is called once per sample.

    wake(project, cfg, parent_running, evidence, attempt) and
    notify(project, evidence) are the only side effects.
    """

    def __init__(self, config, wake, notify, *, log=None):
        self.config, self.wake, self.notify = config, wake, notify
        self.log = log or logging.getLogger("idle-watch")
        self._st = {}
        self._since = {}  # (project, agent) -> (state, first_seen)

    def tick(self, rows, ts, now):
        for project, cfg in self.config.items():
            self._tick_project(project, cfg, rows, ts, now)

    def _tick_project(self, project, cfg, rows, ts, now):
        st = self._st.setdefault(project, dict(cls=None, n=0, since=0.0,
                                               fired=0, next=0.0, asked=False))
        if rows is None or now - ts > cfg["stale"]:
            cls, states = "unknown", {}   # unreadable or stale data is never idle
        else:
            cls, states = classify(rows, project, cfg["parent"])
        for name, state in states.items():
            old = self._since.get((project, name))
            if old is None or old[0] != state:
                self._since[(project, name)] = (state, now)
        if cls != st["cls"]:
            # blocked<->idle also ends the episode: ask must never resume.
            st.update(cls=cls, n=0, since=now, fired=0, next=0.0, asked=False)
        st["n"] += 1
        if cls not in ("idle", "blocked"):
            return
        if st["n"] < cfg["samples"] or now - st["since"] < cfg["seconds"]:
            return
        evidence = {name: dict(state=s, seconds=int(now - self._since[(project, name)][1]))
                    for name, s in states.items()}
        if cls == "blocked":
            if not st["asked"]:
                st["asked"] = True
                self.notify(project, evidence)
            return
        if st["fired"] == 0 or (st["fired"] <= cfg["max_resends"] and now >= st["next"]):
            delay = min(cfg["resend_after"] * 2 ** st["fired"], cfg["resend_cap"])
            st["fired"] += 1
            st["next"] = now + delay
            self.wake(project, cfg, cfg["parent"] in states, evidence, st["fired"])
