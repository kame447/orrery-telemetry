"""A Mail signal reaches only the proven recipient in its own project.

The real watcher runs against a scripted tmux (exact sessions, concrete panes,
per-session environment) and a local fake ORRERY Mail (token whois and the
project resource). Assertions are on what reaches tmux and on delivery state.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WATCHER = ROOT / "hooks" / "watch_agent_mail_signals.sh"
AGENT = "Brisk-Curie"
TOKEN = "recipient-owner-token"

FAKE_TMUX = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_TMUX_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(os.environ["FAKE_TMUX_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")

def save():
    json.dump(state, open(state_path, "w"))

def target_session(target):
    for name, session in state["sessions"].items():
        if target == "=" + name or target == session["pane"]:
            return name, session
    return None, None

def mutate(moment):
    change = state.get("mutate") or {}
    if change.get("on") == moment:
        state["sessions"][change["session"]].update(change.get("set") or {})
        if change.get("file"):
            with open(change["file"], "w") as handle:
                handle.write(change["content"])
        state["mutate"] = None
        save()

def option(flag):
    return args[args.index(flag) + 1] if flag in args else ""

command = args[0]
name, session = target_session(option("-t"))
if command == "has-session":
    sys.exit(0 if session else 1)
if session is None:
    sys.exit(1)
if command == "display-message":
    text = args[-1]
    for key, value in (("#{pane_id}", session["pane"]), ("#{session_id}", session["id"]),
                       ("#{session_name}", name), ("#{pane_current_path}", session["cwd"])):
        text = text.replace(key, value)
    print(text)
elif command == "show-environment":
    variable = args[-1]
    if variable not in session["env"]:
        sys.exit(1)
    print(f"{variable}={session['env'][variable]}")
elif command == "capture-pane":
    print("❯ ")
    mutate("capture")
elif command == "send-keys":
    if "-l" in args:
        mutate("send-literal")
else:
    sys.exit(1)
'''


class FakeMail:
    def __init__(self) -> None:
        self.owners: dict[tuple[str, str], str] = {}
        self.projects: dict[str, dict] = {}
        self.requests: list[dict] = []
        # Mail stops accepting the token after this many proofs (a retirement
        # or re-registration during delivery); None keeps accepting.
        self.whois_accepts: int | None = None
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                parent.requests.append(body)
                reply = parent.answer(body)
                data = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), **reply}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def answer(self, body: dict) -> dict:
        params = body.get("params") or {}
        if body.get("method") == "resources/read":
            slug = params["uri"].removeprefix("resource://project/")
            if slug not in self.projects:
                return {"error": {"code": -32000, "message": "not found"}}
            return {"result": {"contents": [{"uri": params["uri"],
                                             "text": json.dumps(self.projects[slug])}]}}
        args = params.get("arguments") or {}
        if params.get("name") == "whois":
            key = (args.get("project_key"), args.get("agent_name"))
            accepted = len(self.whois_projects()) <= (self.whois_accepts
                                                      if self.whois_accepts is not None else 1 << 30)
            if accepted and args.get("registration_token") and self.owners.get(key) == args["registration_token"]:
                return {"result": {"structuredContent": {"name": args["agent_name"]}}}
            return {"error": {"code": -32000, "message": "invalid registration_token"}}
        return {"error": {"code": -32601, "message": "unexpected"}}

    def whois_projects(self) -> list[str]:
        return [r["params"]["arguments"]["project_key"] for r in self.requests
                if (r.get("params") or {}).get("name") == "whois"]

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/mcp"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()


def git(*args, cwd=None, env=None):
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture
def world(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AGENTSTACK_", "GIT_")) and key not in {
               "HOME", "PROJECT_KEY", "AGENT_NAME", "TMUX", "TMUX_PANE",
               "CHILD_REGISTRATION_TOKEN", "MCP_AGENT_MAIL_TOKEN", "MCP_URL", "BASH_ENV", "ENV"}}
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "tmux").write_text(FAKE_TMUX, encoding="utf-8")
    (bindir / "tmux").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    # No fswatch on this PATH: the watcher polls.
    python_dir = pathlib.Path(sys.executable).parent
    env.update(HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               PATH=f"{bindir}:{python_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
               AGENTSTACK_SIGNALS_DIR=str(tmp_path / "signals"),
               AGENTSTACK_RUNTIME_DIR=str(runtime),
               AGENTSTACK_MAIL_WATCHER_LOCK_DIR=str(tmp_path / "watcher.lock"),
               AGENTSTACK_MAIL_ENV=str(tmp_path / "missing-mail.env"),
               FAKE_TMUX_STATE=str(tmp_path / "tmux-state.json"),
               FAKE_TMUX_LOG=str(tmp_path / "tmux.log"),
               TMUX_TIMEOUT="5")
    repos = {}
    for name in ("alpha", "beta"):
        repo = tmp_path / name
        git("init", "-q", str(repo), env=env)
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "--allow-empty", "-qm", "init", cwd=repo, env=env)
        repos[name] = repo.resolve()
    linked = tmp_path / "alpha-linked"
    git("worktree", "add", "-q", "-b", "linked", str(linked), cwd=repos["alpha"], env=env)
    world = {"env": env, "tmp": tmp_path, "runtime": runtime, "signals": tmp_path / "signals",
             "alpha": repos["alpha"], "beta": repos["beta"], "linked": linked.resolve()}
    set_sessions(world, {})
    write_token(world)
    return world


def set_sessions(world, sessions, mutate=None):
    state = {"sessions": {}, "mutate": mutate}
    for index, (name, spec) in enumerate(sessions.items(), start=3):
        state["sessions"][name] = {"pane": f"%{index}", "id": f"${index}",
                                   "cwd": str(spec["cwd"]),
                                   "env": {k: str(v) for k, v in spec.get("env", {}).items()}}
    pathlib.Path(world["env"]["FAKE_TMUX_STATE"]).write_text(json.dumps(state), encoding="utf-8")


def launcher_env(key, repository=None):
    """What agent-start exports into the session for a recipient."""
    env = {"AGENTSTACK_PROJECT_KEY": key, "PROJECT_KEY": key}
    if repository is not None:
        env["AGENTSTACK_PROJECT_REPOSITORY"] = repository
    return env


def write_token(world, token=TOKEN, mode=0o600, name=AGENT):
    path = world["runtime"] / f"agent_token_{name}"
    if path.is_symlink() or path.exists():
        path.unlink()
    path.write_text(token, encoding="utf-8")
    path.chmod(mode)
    return path


def write_signal(world, project_key, *, slug="alpha-slug", msg_id=42, agent=AGENT,
                 importance="high"):
    path = world["signals"] / "projects" / slug / "agents" / agent / f"{msg_id}.signal"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"timestamp": "2026-09-17T00:00:00+00:00", "project": slug, "agent": agent,
                "message": {"id": msg_id, "from": "Parent-Bohr",
                            "subject": "phase5 check", "importance": importance,
                            "body_snippet": "hello", "body_truncated": False}}
    if project_key is not None:
        document["project_key"] = str(project_key)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def state_key(project, msg_id=42, agent=AGENT):
    return json.dumps([str(project), agent, str(msg_id)], ensure_ascii=False)


def read_state(world):
    try:
        return json.loads((world["runtime"] / "notify-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_watcher(world, mail, settled, timeout=25):
    """Run the real watcher until every key in `settled` has a result."""
    env = dict(world["env"], AGENTSTACK_MCP_URL=mail.url)
    watcher = subprocess.Popen(["/bin/bash", str(WATCHER)], env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = read_state(world)
            if all(state.get(key, {}).get("last_result") for key in settled):
                time.sleep(0.3)
                break
            time.sleep(0.1)
    finally:
        watcher.terminate()
        output, _ = watcher.communicate(timeout=15)
    return read_state(world), output


def tmux_calls(world):
    path = pathlib.Path(world["env"]["FAKE_TMUX_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def touched_panes(world):
    """capture-pane / send-keys calls: anything that reads or writes a pane."""
    return [call for call in tmux_calls(world) if call[0] in {"capture-pane", "send-keys"}]


def assert_delivered(world, state, project, pane="%3", msg_id=42):
    assert state[state_key(project, msg_id)]["last_result"] == "success"
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    assert [call[:3] for call in sends] == [["send-keys", "-t", pane], ["send-keys", "-t", pane]]
    assert "message from Parent-Bohr [high]: phase5 check" in sends[0][-1]
    assert sends[1][-1] == "C-m"
    for call in tmux_calls(world):
        if call[0] in {"has-session", "show-environment"}:
            assert call[2] == f"={AGENT}"


def assert_pending(world, state, project, result, signal):
    assert state[state_key(project)]["last_result"] == result
    assert signal.exists()


# --- valid recipients -------------------------------------------------------


@pytest.mark.parametrize("case", ["launcher", "linked-worktree", "no-session-context"])
def test_a_proven_recipient_receives_the_signal(world, case):
    alpha = world["alpha"]
    cwd = world["linked"] if case == "linked-worktree" else alpha
    env = {} if case == "no-session-context" else launcher_env(alpha, alpha)
    set_sessions(world, {AGENT: {"cwd": cwd, "env": env}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, output = run_watcher(world, mail, [state_key(alpha)])
    assert_delivered(world, state, alpha)
    # One proof before the capture and one before each of the two writes.
    assert mail.whois_projects() == [str(alpha)] * 3
    assert not signal.exists()
    assert TOKEN not in output


def test_a_logical_namespace_recipient_receives_the_signal(world):
    set_sessions(world, {AGENT: {"cwd": world["alpha"], "env": launcher_env("team-x", world["alpha"])}})
    write_signal(world, "team-x")
    with FakeMail() as mail:
        mail.owners[("team-x", AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key("team-x")])
    assert_delivered(world, state, "team-x")


# --- refused recipients: nothing is read from or typed into the pane ---------


@pytest.mark.parametrize("case", [
    "same-name-in-another-project", "contradictory-session-context",
    "no-session-context-elsewhere", "logical-key-bound-to-another-repository",
    "wrong-token", "missing-token", "unsafe-token-mode", "symlinked-token",
])
def test_an_unproven_recipient_is_never_touched(world, case):
    alpha, beta = world["alpha"], world["beta"]
    signal_project = "team-x" if case == "logical-key-bound-to-another-repository" else alpha
    session = {"cwd": alpha, "env": launcher_env(alpha, alpha)}
    owners = {(str(signal_project), AGENT): TOKEN}
    if case == "same-name-in-another-project":
        session = {"cwd": beta, "env": launcher_env(beta, beta)}
        owners[(str(beta), AGENT)] = TOKEN
    elif case == "contradictory-session-context":
        session = {"cwd": beta, "env": launcher_env(alpha, alpha)}
    elif case == "no-session-context-elsewhere":
        session = {"cwd": beta, "env": {}}
    elif case == "logical-key-bound-to-another-repository":
        session = {"cwd": alpha, "env": launcher_env("team-x", beta)}
    elif case == "wrong-token":
        owners = {(str(alpha), AGENT): "someone-elses-token"}
    elif case == "missing-token":
        (world["runtime"] / f"agent_token_{AGENT}").unlink()
    elif case == "unsafe-token-mode":
        write_token(world, mode=0o644)
    elif case == "symlinked-token":
        target = write_token(world, name="Elsewhere")
        link = world["runtime"] / f"agent_token_{AGENT}"
        link.unlink()
        link.symlink_to(target)
    set_sessions(world, {AGENT: session})
    signal = write_signal(world, signal_project)
    with FakeMail() as mail:
        mail.owners.update(owners)
        state, output = run_watcher(world, mail, [state_key(signal_project)])
    assert_pending(world, state, signal_project, "recipient_unverified", signal)
    assert touched_panes(world) == []
    if case in {"missing-token", "unsafe-token-mode", "symlinked-token",
                "same-name-in-another-project", "contradictory-session-context",
                "no-session-context-elsewhere", "logical-key-bound-to-another-repository"}:
        assert mail.whois_projects() == []
    assert TOKEN not in output


@pytest.mark.parametrize("marker", [
    "PROJECT_KEY", "AGENTSTACK_PROJECT_REPOSITORY", "AGENTSTACK_PROJECT_WORK_DIR",
    "AGENTSTACK_PROJECT_WORKTREE_ROOT", "AGENTSTACK_PROTECTED_ROOTS",
])
def test_a_foreign_marker_is_not_masked_by_a_valid_key(world, marker):
    """A valid key for this pane cannot make another project's marker moot."""
    alpha, beta = world["alpha"], world["beta"]
    env = launcher_env(alpha, alpha)
    env[marker] = str(beta)
    set_sessions(world, {AGENT: {"cwd": alpha, "env": env}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_unverified", signal)
    assert touched_panes(world) == []
    assert mail.whois_projects() == []


@pytest.mark.parametrize("marker", ["AGENTSTACK_PROJECT_WORKTREE_ROOT", "AGENTSTACK_PROTECTED_ROOTS"])
def test_a_linked_worktree_marker_of_the_same_repository_still_delivers(world, marker):
    alpha = world["alpha"]
    env = launcher_env(alpha, alpha)
    env[marker] = str(world["linked"])
    set_sessions(world, {AGENT: {"cwd": world["linked"], "env": env}})
    write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_delivered(world, state, alpha)


def test_contradictory_markers_are_refused_without_a_key(world):
    """The marker-free fallback does not extend to partial foreign markers."""
    alpha, beta = world["alpha"], world["beta"]
    set_sessions(world, {AGENT: {"cwd": alpha, "env": {"AGENTSTACK_PROJECT_REPOSITORY": str(beta)}}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_unverified", signal)
    assert touched_panes(world) == []


# --- evidence re-checked around the delivery delay ---------------------------


@pytest.mark.parametrize("accepts, expected_sends", [(0, 0), (1, 0), (2, 1)])
def test_mail_must_still_accept_the_token_at_every_step(world, accepts, expected_sends):
    """A registration retired mid-delivery stops the next write.

    One proof happens before the capture, one before the literal text and one
    before the submit, so `accepts` decides how far delivery gets.
    """
    alpha = world["alpha"]
    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        mail.whois_accepts = accepts
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    result = "recipient_unverified" if accepts == 0 else "recipient_changed"
    assert_pending(world, state, alpha, result, signal)
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    assert len(sends) == expected_sends
    assert all(call[-1] != "C-m" for call in sends)
    if accepts == 0:
        assert touched_panes(world) == []



@pytest.mark.parametrize("moment", ["capture", "send-literal"])
def test_a_recipient_that_changes_during_delivery_is_not_submitted_to(world, moment):
    alpha, beta = world["alpha"], world["beta"]
    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)}},
                 mutate={"on": moment, "session": AGENT, "set": {"cwd": str(beta)}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_changed", signal)
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    if moment == "capture":
        assert sends == []
    else:
        assert len(sends) == 1 and "-l" in sends[0], "the typed text is never submitted"


@pytest.mark.parametrize("change", ["pane-replaced", "token-replaced"])
def test_other_evidence_changing_after_the_first_write_stops_the_submit(world, change):
    alpha = world["alpha"]
    mutate = {"on": "send-literal", "session": AGENT}
    if change == "pane-replaced":
        mutate["set"] = {"pane": "%9"}
    else:
        mutate["file"] = str(world["runtime"] / f"agent_token_{AGENT}")
        mutate["content"] = "a-newer-registration"
    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)}}, mutate=mutate)
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_changed", signal)
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    assert len(sends) == 1 and "-l" in sends[0]


# --- project-scoped state ---------------------------------------------------


def test_same_agent_and_message_in_two_projects_are_tracked_apart(world):
    alpha, beta = world["alpha"], world["beta"]
    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)}})
    # A stale global entry and a success recorded for the other project must
    # not suppress this project's delivery.
    (world["runtime"] / "notify-state.json").write_text(json.dumps({
        f"{AGENT}:42": {"last_result": "success", "last_attempt_epoch": int(time.time())},
    }), encoding="utf-8")
    write_signal(world, beta, slug="beta-slug")
    write_signal(world, alpha, slug="alpha-slug")
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha), state_key(beta)])
    assert state[state_key(beta)]["last_result"] == "recipient_unverified"
    assert_delivered(world, state, alpha)
    assert state[f"{AGENT}:42"]["last_result"] == "success"  # left untouched
    assert (world["signals"] / "projects" / "beta-slug" / "agents" / AGENT / "42.signal").exists()
    assert list((world["runtime"] / "notify-locks").iterdir()) == []


def test_a_logical_key_with_separators_does_not_alias_another_entry(world):
    tricky = "team:x"
    set_sessions(world, {AGENT: {"cwd": world["alpha"], "env": launcher_env(tricky, world["alpha"])}})
    (world["runtime"] / "notify-state.json").write_text(json.dumps({
        json.dumps(["team", f"x:{AGENT}", "42"]): {"last_result": "success",
                                                  "last_attempt_epoch": int(time.time())},
    }), encoding="utf-8")
    write_signal(world, tricky)
    with FakeMail() as mail:
        mail.owners[(tricky, AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(tricky)])
    assert_delivered(world, state, tricky)


# --- legacy slug-only signals ------------------------------------------------


@pytest.mark.parametrize("answer", ["match", "slug-mismatch", "missing"])
def test_a_slug_only_signal_needs_mail_to_name_its_project(world, answer):
    alpha = world["alpha"]
    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)}})
    signal = write_signal(world, None, slug="alpha-slug")
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        if answer == "match":
            mail.projects["alpha-slug"] = {"slug": "alpha-slug", "human_key": str(alpha)}
        elif answer == "slug-mismatch":
            mail.projects["alpha-slug"] = {"slug": "other-slug", "human_key": str(alpha)}
        if answer == "match":
            state, _ = run_watcher(world, mail, [state_key(alpha)])
        else:
            state, _ = run_watcher(world, mail, [], timeout=5)
    if answer == "match":
        assert_delivered(world, state, alpha)
    else:
        assert state == {}
        assert tmux_calls(world) == []
        assert signal.exists()


# --- producer ----------------------------------------------------------------


def test_the_producer_writes_the_canonical_project_key(tmp_path):
    from types import SimpleNamespace

    from agentstack_mail import storage

    settings = SimpleNamespace(notifications=SimpleNamespace(
        enabled=True, signals_dir=str(tmp_path), include_metadata=False,
        clear_grace_seconds=0.0, debounce_ms=0))
    emitted = asyncio.run(storage.emit_notification_signal(
        settings, "alpha-slug", AGENT, {"id": 7}, project_key="/work/alpha"))
    assert emitted is True
    document = json.loads((tmp_path / "projects" / "alpha-slug" / "agents" / AGENT / "7.signal")
                          .read_text(encoding="utf-8"))
    assert document["project_key"] == "/work/alpha"
    assert document["project"] == "alpha-slug"
    assert "message" not in document  # independent of optional message metadata


def test_send_message_passes_the_project_human_key():
    source = (ROOT / "packages/agentstack_mail/src/agentstack_mail/app.py").read_text(encoding="utf-8")
    call = source[source.index("await emit_notification_signal("):]
    call = call[:call.index(")")]
    assert "project_key=project.human_key" in call
