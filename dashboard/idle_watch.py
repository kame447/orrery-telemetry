"""Project idle watch: mail a running parent when no agent of a project is working.

Pure decision logic. The caller supplies /api/agents rows and the delivery
callbacks; nothing here types into a pane, reads credentials or touches Mail.
Anything that cannot be attributed or observed is `unknown`, never idle.
"""
from __future__ import annotations

import json
import logging
import urllib.request

DEFAULTS = dict(samples=3, seconds=60, poll=10, stale=30,
                resend_after=600, max_resends=3, resend_cap=3600)
# Inclusive integer range of each setting. Any other value disables the project.
LIMITS = dict(samples=(1, 1000), seconds=(0, 86400), poll=(1, 3600), stale=(1, 3600),
              resend_after=(1, 86400), max_resends=(0, 10), resend_cap=(1, 86400))

_log = logging.getLogger("idle-watch")


def _valid_setting(key, value):
    low, high = LIMITS[key]
    return type(value) is int and low <= value <= high


def load_config(path):
    """Return {project_key: settings} for valid enabled projects; absent file = none.

    Never raises: a malformed file or project is logged and left disabled.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        _log.warning("idle-watch: %s unreadable, disabled: %s", path, exc)
        return {}
    raw = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        _log.warning("idle-watch: %s has no \"projects\" object, disabled", path)
        return {}
    out = {}
    for key, value in raw.items():
        if not (isinstance(value, dict) and value.get("enabled") is True):
            continue
        settings = {k: value.get(k, d) for k, d in DEFAULTS.items()}
        bad = [k for k, v in settings.items() if not _valid_setting(k, v)]
        parent = value.get("parent")
        if bad or not (isinstance(parent, str) and parent):
            _log.warning("idle-watch: project %s disabled, invalid %s", key,
                         ", ".join(bad) or "parent")
            continue
        out[key] = {**settings, "parent": parent}
    return out


def fetch_rows(port, timeout=20):
    url = f"http://127.0.0.1:{port}/api/agents?scope=live"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read())
    return payload["agents"], float(payload["ts"])


def classify(rows, project, parent):
    """Return (class, states). class: busy | idle | blocked | unknown.

    Members are the project's rows; the parent must be one of them. The
    result is unknown, never idle, when: the parent row is absent, not
    running, or not of this project (another project, or project_key null
    because the name is unregistered or registered in several projects);
    a member is not running (liveness unknown) or is
    retired; a Mail-linked row of unknown project (e.g. a name registered in
    several projects) is anything but a running `wait`; or any member state
    is other than work/wait/ask.
    """
    states, parent_seen = {}, False
    for row in rows:
        name, key = row.get("name"), row.get("project_key")
        if name == parent:
            if key != project:
                return "unknown", {}
            parent_seen = True
        elif key is None:
            if row.get("mail_linked") and not (row.get("running") and row.get("act_state") == "wait"):
                return "unknown", {}
            continue
        elif key != project:
            continue
        if not row.get("running") or row.get("retired_but_alive"):
            return "unknown", {}
        states[name] = row.get("act_state")
    if "work" in states.values():
        return "busy", states
    if not parent_seen or any(s not in ("wait", "ask") for s in states.values()):
        return "unknown", states
    return ("blocked" if "ask" in states.values() else "idle"), states


def fresh(ts, now, stale):
    """A sample counts only if its timestamp is not in the future and not stale."""
    return 0 <= now - ts <= stale


class IdleWatch:
    """One state machine per project; tick() is called once per sample.

    wake(project, cfg, evidence, attempt) -> status and notify(project,
    evidence) are the only side effects. Only status "sent" allows a later
    resend; any other status (delivery unknown, rejected, no sender, state
    changed) stops sending until work is observed again. The send budget is
    reset only by an observed `work`, never by unknown or blocked samples.
    """

    def __init__(self, config, wake, notify, *, log=None):
        self.config, self.wake, self.notify = config, wake, notify
        self.log = log or _log
        self._st = {}
        self._since = {}  # (project, agent) -> (state, first_seen)
        self._work_at = {}  # (project, agent) -> last time seen working

    def tick(self, rows, ts, now):
        for project, cfg in self.config.items():
            self._tick_project(project, cfg, rows, ts, now)

    def _tick_project(self, project, cfg, rows, ts, now):
        st = self._st.setdefault(project, dict(cls=None, n=0, since=0.0,
                                               fired=0, next=0.0, asked=False))
        if rows is None or not fresh(ts, now, cfg["stale"]):
            cls, states = "unknown", {}   # unreadable, stale or future data is never idle
        else:
            cls, states = classify(rows, project, cfg["parent"])
        for name, state in states.items():
            if state == "work":
                self._work_at[(project, name)] = now
            old = self._since.get((project, name))
            if old is None or old[0] != state:
                self._since[(project, name)] = (state, now)
        if cls == "busy":
            st.update(fired=0, next=0.0)  # only real work re-arms the send budget
        if cls != st["cls"]:
            # Any change restarts the streak; blocked<->idle too: ask must never resume.
            st.update(cls=cls, n=0, since=now, asked=False)
        st["n"] += 1
        if cls not in ("idle", "blocked"):
            return
        if st["n"] < cfg["samples"] or now - st["since"] < cfg["seconds"]:
            return
        evidence = {name: dict(state=s, seconds=int(now - self._since[(project, name)][1]),
                               last_work=self._work_at.get((project, name)))
                    for name, s in states.items()}
        if cls == "blocked":
            if not st["asked"]:
                st["asked"] = True
                self.notify(project, evidence)
            return
        if st["fired"] > cfg["max_resends"] or now < st["next"]:
            return
        st["fired"] = attempt = st["fired"] + 1
        st["next"] = now + min(cfg["resend_after"] * 2 ** (attempt - 1), cfg["resend_cap"])
        status = "exception"
        try:
            status = self.wake(project, cfg, evidence, attempt)
        finally:
            if status != "sent":
                st["fired"] = cfg["max_resends"] + 1   # no automatic resend
                self.log.warning("idle-watch: %s wake attempt %d -> %s; no resend until work",
                                 project, attempt, status)
