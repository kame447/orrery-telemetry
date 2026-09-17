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
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

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
    """Resolve a target-session: an exact "=name", or a session id."""
    for name, session in state["sessions"].items():
        if target in ("=" + name, session["id"]):
            return name, session
    return None, None

def target_pane(target):
    """Resolve a target-pane the way real tmux does.

    A pane id names the pane. "=name:" is the current pane of that exact
    session. A bare "=name" is a target-session: real tmux does not resolve it
    to a pane, so neither does this fake -- accepting it here is what hid a
    production defect that every real session hit.
    """
    for name, session in state["sessions"].items():
        if target in (session["pane"], "=" + name + ":", session["id"] + ":"):
            return name, session
    return None, None

def mutate(moment):
    change = state.get("mutate") or {}
    if change.get("on") != moment:
        return
    if change.get("skip"):  # fire on a later occurrence of the same moment
        change["skip"] -= 1
        save()
        return
    # A rename or a rebinding of the agent name to another session.
    for old, new in (change.get("rename") or {}).items():
        state["sessions"][new] = state["sessions"].pop(old)
    if change.get("session"):
        state["sessions"][change["session"]].update(change.get("set") or {})
    if change.get("file"):
        with open(change["file"], "w") as handle:
            handle.write(change["content"])
    state["mutate"] = None
    save()

def option(flag):
    return args[args.index(flag) + 1] if flag in args else ""

command = args[0]
target = option("-t")
if command in ("display-message", "capture-pane", "send-keys"):
    name, session = target_pane(target)
else:
    name, session = target_session(target)
if command == "has-session":
    sys.exit(0 if session else 1)
if session is None:
    # Real tmux prints an empty line and succeeds for display-message with an
    # unresolvable pane target, and fails for the others.
    if command == "display-message":
        print()
        sys.exit(0)
    sys.exit(1)
if command == "display-message":
    text = args[-1]
    for key, value in (("#{pane_id}", session["pane"]), ("#{session_id}", session["id"]),
                       ("#{session_name}", name), ("#{pane_current_path}", session["cwd"])):
        text = text.replace(key, value)
    print(text)
elif command == "show-environment":
    # Real tmux fails the whole call when the server or the target is gone;
    # env_fail scripts that. It prints NAME=value for every set variable and
    # -NAME for an unset one.
    if name in (state.get("env_fail") or []):
        remaining = state.get("env_fail_after") or 0
        if remaining:
            state["env_fail_after"] = remaining - 1
            save()
        else:
            if state.get("env_fail_mode") == "print":
                for key, value in session["env"].items():
                    print(f"{key}={value}")
            sys.exit(1)
    if len(args) > 3:
        variable = args[-1]
        if variable not in session["env"]:
            sys.exit(1)
        print(f"{variable}={session['env'][variable]}")
    else:
        for key, value in session["env"].items():
            print(f"{key}={value}")
        for key in session.get("unset") or []:
            print(f"-{key}")
    mutate("env")
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
        # Mail stops accepting the credential after this many proofs: the
        # registration was deleted, or the name re-registered with another
        # token. (A soft retirement is not this: it keeps the row and its token
        # binding, so a retired agent still proves ownership.) None keeps
        # accepting.
        self.whois_accepts: int | None = None
        # Fired after each whois proof, so a test can move the recipient
        # underneath the watcher while the proof is in flight.
        self.on_whois = None
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
            proofs = len(self.whois_projects())
            accepted = proofs <= (self.whois_accepts
                                  if self.whois_accepts is not None else 1 << 30)
            if self.on_whois is not None:
                self.on_whois(proofs)
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


def set_sessions(world, sessions, mutate=None, env_fail=(), env_fail_after=0,
                 env_fail_mode="silent"):
    state = {"sessions": {}, "mutate": mutate, "env_fail": list(env_fail),
             "env_fail_after": env_fail_after, "env_fail_mode": env_fail_mode}
    for index, (name, spec) in enumerate(sessions.items(), start=3):
        state["sessions"][name] = {"pane": f"%{index}", "id": f"${index}",
                                   "cwd": str(spec["cwd"]),
                                   "env": {k: str(v) for k, v in spec.get("env", {}).items()},
                                   "unset": list(spec.get("unset") or [])}
    pathlib.Path(world["env"]["FAKE_TMUX_STATE"]).write_text(json.dumps(state), encoding="utf-8")


def launcher_env(key, repository=None):
    """What agent-start exports into the session for a recipient."""
    env = {"AGENTSTACK_PROJECT_KEY": str(key), "PROJECT_KEY": str(key)}
    if repository is not None:
        env["AGENTSTACK_PROJECT_REPOSITORY"] = str(repository)
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


def install_quiet_fswatch(world):
    """Make the watcher use its event backend with an event that never comes.

    With `fswatch` on PATH the watcher scans once at startup and then waits, its
    next periodic scan being 30 seconds away. That bounds a run to a single
    delivery attempt, which is what a scenario about one attempt can own.
    """
    path = pathlib.Path(world["env"]["PATH"].split(":")[0]) / "fswatch"
    # Bounded on its own as well as killed by the watcher's own cleanup, so a
    # terminated run can never leave a stray backend behind.
    path.write_text("#!/bin/sh\nexec sleep 60\n", encoding="utf-8")
    path.chmod(0o755)


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


def assert_delivered(world, state, project, pane="%3", msg_id=42, session_id="$3"):
    assert state[state_key(project, msg_id)]["last_result"] == "success"
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    assert [call[:3] for call in sends] == [["send-keys", "-t", pane], ["send-keys", "-t", pane]]
    assert "message from Parent-Bohr [high]: phase5 check" in sends[0][-1]
    assert sends[1][-1] == "C-m"
    for call in tmux_calls(world):
        if call[0] == "has-session":
            assert call[2] == f"={AGENT}"
        # The session's own environment is read by the concrete session id the
        # pane reported, never by the mutable agent name, and in one whole
        # snapshot rather than one variable at a time.
        if call[0] == "show-environment":
            assert call == ["show-environment", "-t", session_id]


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


def test_a_coherent_context_without_the_optional_markers_still_delivers(world):
    """Absent optional markers must not shift or invalidate the ones present."""
    alpha = world["alpha"]
    set_sessions(world, {AGENT: {"cwd": alpha, "env": {
        "AGENTSTACK_PROJECT_KEY": str(alpha),
        "AGENTSTACK_PROJECT_REPOSITORY": str(alpha),
        "AGENTSTACK_PROJECT_WORK_DIR": str(alpha)}}})
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


# --- the session environment is read from the concrete session ---------------


@pytest.mark.parametrize("case", ["foreign-markers", "name-moved"])
def test_a_name_rebound_while_the_environment_is_read_is_not_delivered_to(world, case):
    """The agent name may move to another session at any moment.

    The pane is resolved from the name, but its environment is then read by the
    session id that pane itself reported, and the pane's identity is taken
    again afterwards. Reading by `=name` instead would let the session that now
    holds the name answer for the pane that held it a moment ago: in
    `foreign-markers` the old pane belongs to another logical project of the
    same repository, and in `name-moved` its markers are right but the name has
    already moved on, so it is no longer this agent's terminal.
    """
    alpha, linked = world["alpha"], world["linked"]
    install_quiet_fswatch(world)
    old_env = launcher_env("team-b" if case == "foreign-markers" else "team-a", alpha)
    set_sessions(world, {AGENT: {"cwd": alpha, "env": old_env},
                         "Brisk-Curie-new": {"cwd": linked, "env": launcher_env("team-a", alpha)}},
                 mutate={"on": "env", "rename": {AGENT: "Brisk-Curie-old",
                                                 "Brisk-Curie-new": AGENT}})
    signal = write_signal(world, "team-a")
    with FakeMail() as mail:
        mail.owners[("team-a", AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key("team-a")])
    assert_pending(world, state, "team-a", "recipient_unverified", signal)
    assert touched_panes(world) == []


def test_a_failed_environment_read_is_not_an_absent_marker(world):
    """A read that fails must not look like a session that carries no markers.

    The session claims alpha in one variable and beta in another, which the
    coherence rule refuses. If the failure were swallowed, no marker would be
    seen at all and the pane's own directory would authorise the delivery.
    """
    alpha, beta = world["alpha"], world["beta"]
    env = dict(launcher_env(alpha, alpha), PROJECT_KEY=str(beta))
    set_sessions(world, {AGENT: {"cwd": alpha, "env": env}}, env_fail=[AGENT])
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_unverified", signal)
    assert touched_panes(world) == []
    assert mail.whois_projects() == [], "no credential is sent for an unproven recipient"


def test_an_identity_that_moves_during_the_owner_proof_captures_nothing(world):
    """The proof is a round trip; the name can move while it is in flight."""
    alpha, linked = world["alpha"], world["linked"]
    install_quiet_fswatch(world)
    state_path = pathlib.Path(world["env"]["FAKE_TMUX_STATE"])

    def rename_away(proofs):
        if proofs != 1:  # only while the first proof, before any capture, is in flight
            return
        scripted = json.loads(state_path.read_text(encoding="utf-8"))
        scripted["sessions"]["Brisk-Curie-old"] = scripted["sessions"].pop(AGENT)
        scripted["sessions"][AGENT] = scripted["sessions"].pop("Brisk-Curie-new")
        state_path.write_text(json.dumps(scripted), encoding="utf-8")

    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)},
                         "Brisk-Curie-new": {"cwd": linked, "env": launcher_env(alpha, alpha)}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        mail.on_whois = rename_away
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_unverified", signal)
    assert touched_panes(world) == []


def test_a_name_moved_during_the_final_environment_read_captures_nothing(world):
    """The identity is taken after each snapshot, including the last one.

    The environment is re-read once more after the Mail proof; the name is
    rebound to another session while that read is in flight, so the snapshot
    still compares equal and only the identity taken after it can see the move.
    """
    alpha, linked = world["alpha"], world["linked"]
    install_quiet_fswatch(world)
    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env(alpha, alpha)},
                         "Brisk-Curie-new": {"cwd": linked, "env": launcher_env(alpha, alpha)}},
                 mutate={"on": "env", "skip": 1, "rename": {AGENT: "Brisk-Curie-old",
                                                            "Brisk-Curie-new": AGENT}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_unverified", signal)
    assert touched_panes(world) == []


@pytest.mark.parametrize("mode", ["silent", "print"])
def test_a_failed_final_environment_read_captures_nothing(world, mode):
    """The last read's status matters as much as the first read's.

    Comparing the text alone cannot tell the two apart: a marker-free session's
    successful snapshot is empty, which is exactly what a failed read prints,
    and a read can also fail after printing the very same markers. Only the
    command's own status distinguishes a session that carries no markers from
    one whose markers could not be read.
    """
    alpha = world["alpha"]
    env = {} if mode == "silent" else launcher_env(alpha, alpha)
    set_sessions(world, {AGENT: {"cwd": alpha, "env": env}},
                 env_fail=[AGENT], env_fail_after=1, env_fail_mode=mode)
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_unverified", signal)
    assert touched_panes(world) == []


def test_context_that_changes_during_the_owner_proof_captures_nothing(world):
    """The session can re-point only its project variables while Mail answers.

    Its pane, session id, name and directory are all unchanged, so the identity
    check cannot see it; the markers are read again from the same concrete
    session and must still be the ones that were validated.
    """
    alpha = world["alpha"]
    state_path = pathlib.Path(world["env"]["FAKE_TMUX_STATE"])

    def repoint_context(proofs):
        if proofs != 1:  # only while the first proof, before any capture, is in flight
            return
        scripted = json.loads(state_path.read_text(encoding="utf-8"))
        scripted["sessions"][AGENT]["env"] = launcher_env("team-b", alpha)
        state_path.write_text(json.dumps(scripted), encoding="utf-8")

    set_sessions(world, {AGENT: {"cwd": alpha, "env": launcher_env("team-a", alpha)}})
    signal = write_signal(world, "team-a")
    with FakeMail() as mail:
        mail.owners[("team-a", AGENT)] = TOKEN
        mail.on_whois = repoint_context
        state, _ = run_watcher(world, mail, [state_key("team-a")])
    assert_pending(world, state, "team-a", "recipient_unverified", signal)
    assert touched_panes(world) == []


# --- evidence re-checked around the delivery delay ---------------------------


@pytest.mark.parametrize("accepts, expected_sends", [(0, 0), (1, 0), (2, 1)])
def test_mail_must_still_accept_the_token_at_every_step(world, accepts, expected_sends):
    """A credential Mail stops accepting mid-delivery stops the next write.

    Rejection here means the registration was deleted or the name re-registered
    with another token. Soft retirement is deliberately not that case: it keeps
    the row and its token binding, so a retired agent still proves ownership.

    One proof happens before the capture, one before the literal text and one
    before the submit, so `accepts` decides how far delivery gets.
    """
    alpha = world["alpha"]
    install_quiet_fswatch(world)
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
    install_quiet_fswatch(world)
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


@pytest.mark.parametrize("marker", ["AGENTSTACK_PROJECT_WORKTREE_ROOT", "AGENTSTACK_PROTECTED_ROOTS"])
def test_a_same_project_marker_change_between_the_writes_stops_the_submit(world, marker):
    """Every marker the proof read is part of the evidence, not just the key.

    The session moves to another worktree of the same repository between the
    two writes. The project is unchanged, so this is not a foreign delivery,
    but the context the text was typed into is no longer the context that was
    proven, and the submit is withheld.

    Only that one attempt is in question. The session is coherent again once it
    has moved, so a later attempt is entitled to deliver and would overwrite
    this result; the run is bounded to a single attempt rather than pretending
    the later delivery is wrong.
    """
    alpha, linked = world["alpha"], world["linked"]
    install_quiet_fswatch(world)
    env = dict(launcher_env(alpha, alpha))
    env[marker] = str(alpha)
    moved = dict(env)
    moved[marker] = str(linked)
    set_sessions(world, {AGENT: {"cwd": alpha, "env": env}},
                 mutate={"on": "send-literal", "session": AGENT, "set": {"env": moved}})
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, _ = run_watcher(world, mail, [state_key(alpha)])
    assert_pending(world, state, alpha, "recipient_changed", signal)
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    assert len(sends) == 1 and "-l" in sends[0], "the typed text is never submitted"


@pytest.mark.parametrize("change", ["pane-replaced", "token-replaced"])
def test_other_evidence_changing_after_the_first_write_stops_the_submit(world, change):
    alpha = world["alpha"]
    install_quiet_fswatch(world)
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


# --- against real tmux -------------------------------------------------------


@pytest.fixture
def real_tmux(world, tmp_path):
    """A real tmux server of our own, on its own short socket path.

    The fake tmux above accepts whatever grammar it is written to accept, which
    is exactly how a target-pane spelled as a target-session survived every
    test and failed on every real session. Here the watcher drives the actual
    tmux binary; the shim on PATH only pins the socket and records the call.
    """
    if not shutil.which("tmux"):
        pytest.skip("tmux is not installed")
    socket_path = pathlib.Path(tempfile.mkdtemp(prefix="ags-tm-", dir="/tmp")) / "s"
    bindir = pathlib.Path(world["env"]["PATH"].split(":")[0])
    real = shutil.which("tmux", path=os.environ["PATH"])
    (bindir / "tmux").write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_TMUX_LOG"\n'
        f'exec {real} -S "{socket_path}" "$@"\n',
        encoding="utf-8")
    (bindir / "tmux").chmod(0o755)

    def server(*args, check=True):
        return subprocess.run([real, "-S", str(socket_path), *args], text=True,
                              capture_output=True, check=check)

    try:
        yield server
    finally:
        subprocess.run([real, "-S", str(socket_path), "kill-server"],
                       capture_output=True, check=False)
        shutil.rmtree(socket_path.parent, ignore_errors=True)


def _real_session(server, name, cwd, env):
    server("new-session", "-d", "-s", name, "-c", str(cwd), "sleep 300")
    for key, value in env.items():
        server("set-environment", "-t", name, key, str(value))


@pytest.mark.parametrize("case", ["exact-session", "prefix-only", "foreign-project"])
def test_the_watcher_delivers_through_real_tmux(world, real_tmux, case):
    """Real tmux, real session, real pane.

    `=name` is a target-session: real tmux answers `display-message -p -t
    "=name" '#{pane_id}'` with an empty line and exit 0, and refuses the same
    target for `capture-pane`. Only `=name:` names that exact session's pane.
    A session whose name merely starts with the agent's name must not answer
    either, which is what `prefix-only` holds the line on.
    """
    alpha, beta = world["alpha"], world["beta"]
    if case == "prefix-only":
        # The agent's own session does not exist; another one starts with its name.
        _real_session(real_tmux, f"{AGENT}-extra", alpha, launcher_env(alpha, alpha))
    else:
        _real_session(real_tmux, AGENT, alpha,
                      launcher_env(beta if case == "foreign-project" else alpha, alpha))
        # A same-prefix neighbour must never be the one that answers.
        _real_session(real_tmux, f"{AGENT}-extra", beta, launcher_env(beta, beta))
    signal = write_signal(world, alpha)
    with FakeMail() as mail:
        mail.owners[(str(alpha), AGENT)] = TOKEN
        state, output = run_watcher(world, mail, [state_key(alpha)])

    calls = [line.split() for line in
             pathlib.Path(world["env"]["FAKE_TMUX_LOG"]).read_text(encoding="utf-8").splitlines()]
    touched = [call for call in calls if call[0] in {"capture-pane", "send-keys"}]
    if case == "exact-session":
        assert state[state_key(alpha)]["last_result"] == "success"
        pane = real_tmux("display-message", "-p", "-t", f"={AGENT}:",
                         "#{pane_id}").stdout.strip()
        assert pane.startswith("%")
        sends = [call for call in calls if call[0] == "send-keys"]
        assert [call[:3] for call in sends] == [["send-keys", "-t", pane]] * 2
        assert sends[1][-1] == "C-m"
        assert not signal.exists()
    else:
        expected = "session_not_found" if case == "prefix-only" else "recipient_unverified"
        assert_pending(world, state, alpha, expected, signal)
        assert touched == []
    assert TOKEN not in output


# --- against the real bundled server -----------------------------------------


def _phase4_proof_helpers():
    """The bundled-server helpers the Phase 4 owner proof already uses."""
    import importlib.util

    path = ROOT / "packages/agentstack_mail/tests/test_whois_owner_proof.py"
    spec = importlib.util.spec_from_file_location("agentstack_whois_owner_proof", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundled_mail(tmp_path):
    """The real `agentstack_mail.cli` server on an isolated database and home."""
    helpers = _phase4_proof_helpers()
    port = helpers._free_port()
    root = tmp_path / "bundled"
    (root / "home").mkdir(parents=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENTSTACK_")}
    env.update(HOME=str(root / "home"),
               PATH=f"{pathlib.Path(sys.executable).parent}:/usr/bin:/bin",
               PYTHONPATH=str(ROOT / "packages" / "agentstack_mail" / "src"),
               AGENTSTACK_MAIL_ENV_FILE=str(root / "missing.env"),
               AGENTSTACK_MAIL_DATABASE_URL=f"sqlite+aiosqlite:///{root / 'service.sqlite3'}",
               AGENTSTACK_MAIL_STORAGE_ROOT=str(root / "archive"),
               AGENTSTACK_MAIL_NOTIFICATIONS_ENABLED="false",
               AGENTSTACK_MAIL_TOOLS_LOG_ENABLED="false",
               AGENTSTACK_MAIL_HTTP_HOST="127.0.0.1",
               AGENTSTACK_MAIL_HTTP_PORT=str(port),
               AGENTSTACK_MAIL_HTTP_PATH="/mcp",
               AGENTSTACK_MAIL_AGENT_NAME_ENFORCEMENT_MODE="passthrough")
    process = subprocess.Popen([sys.executable, "-m", "agentstack_mail.cli"], env=env,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        helpers._wait_ready(url, process)
        yield SimpleNamespace(url=url, call=lambda tool, arguments: helpers._call(url, tool, arguments))
    finally:
        process.terminate()
        try:
            process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("credential", ["owner", "stranger"])
def test_the_watcher_proves_its_recipient_against_the_real_bundled_server(world, bundled_mail,
                                                                         credential):
    """The fake Mail above answers a schema; this is the distributed server.

    A delivery is only real if the bundled `whois` accepts the agent's private
    token for this project, so both answers are exercised end to end.
    """
    alpha = world["alpha"]
    project = str(alpha)
    bundled_mail.call("ensure_project", {"human_key": project})
    registered = bundled_mail.call("register_agent", {
        "project_key": project, "program": "claude-code", "model": "test-model",
        "name": AGENT, "task_description": "notification recipient",
        "registration_token": TOKEN})
    structured = (registered.get("result") or {}).get("structuredContent") or {}
    # The bundled server issues the canonical spelling of the name it accepted.
    name = structured["name"]

    write_token(world, TOKEN if credential == "owner" else "someone-elses-token", name=name)
    set_sessions(world, {name: {"cwd": alpha, "env": launcher_env(alpha, alpha)}})
    signal = write_signal(world, alpha, agent=name)
    key = state_key(alpha, agent=name)
    state, output = run_watcher(world, bundled_mail, [key])
    sends = [call for call in tmux_calls(world) if call[0] == "send-keys"]
    if credential == "owner":
        assert state[key]["last_result"] == "success"
        assert [call[:3] for call in sends] == [["send-keys", "-t", "%3"]] * 2
        assert sends[1][-1] == "C-m"
        assert not signal.exists()
    else:
        assert state[key]["last_result"] == "recipient_unverified"
        assert signal.exists()
        assert touched_panes(world) == []
    assert TOKEN not in output


# --- producer ----------------------------------------------------------------


def test_the_producer_writes_the_canonical_project_key(tmp_path):
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
