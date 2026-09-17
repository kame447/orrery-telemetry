"""A delegated child's launch, handoff and cleanup act only in its own project.

Every case runs the real scripts against a local fake ORRERY Mail and fake
tmux/provider binaries, and asserts the side effects that matter: Mail
requests, tmux sessions, and the child's local credential files.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
LIB = ROOT / "bin" / "lib" / "agentstack-register.sh"
PREREGISTER = ROOT / "bin" / "agentstack-preregister-child"
SPAWN = HOOKS / "spawn_child.sh"
CLEANUP = HOOKS / "cleanup-child-agent.sh"
GEMINI_PREREGISTERED = HOOKS / "spawn_gemini_preregistered.sh"
GEMINI_CHILD = HOOKS / "spawn_gemini_child.sh"

CHILD = "Brisk-Curie"
TOKEN = "child-owner-token"

# A stand-in for curl in harnesses without a Mail server: whois accepts the
# token only for the child named in the request (FAKE_CURL_REJECT=1 rejects).
FAKE_CURL = r"""#!/usr/bin/env python3
import json, os, sys
payload = json.loads(sys.stdin.read() or "{}")
params = payload.get("params") or {}
args = params.get("arguments") or {}
log = os.environ.get("FAKE_CURL_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"tool": params.get("name"),
                                 "project_key": args.get("project_key"),
                                 "agent_name": args.get("agent_name")}) + "\n")
if params.get("name") == "whois" and args.get("registration_token"):
    if os.environ.get("FAKE_CURL_REJECT") == "1":
        print(json.dumps({"jsonrpc": "2.0", "id": "1",
                          "error": {"code": -32000, "message": "invalid registration_token"}}))
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": "1",
                          "result": {"structuredContent": {"name": args.get("agent_name")}}}))
else:
    print(json.dumps({"jsonrpc": "2.0", "id": "1", "result": {"structuredContent": {}}}))
"""


def install_fake_curl(bindir: pathlib.Path) -> None:
    path = bindir / "curl"
    path.write_text(FAKE_CURL, encoding="utf-8")
    path.chmod(0o755)


class FakeMail:
    """ORRERY Mail that knows which token owns which name in which project."""

    def __init__(self) -> None:
        self.owners: dict[tuple[str, str], str] = {}
        self.requests: list[dict] = []
        self.on_call = None
        self.retire_failure = ""  # "", "tool-error", "http-500" or "drop"
        self.rename: dict[str, str] = {}  # requested name -> returned name
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                params = body.get("params") or {}
                name, args = params.get("name"), dict(params.get("arguments") or {})
                parent.requests.append({"tool": name, "args": args})
                if parent.on_call:
                    parent.on_call(name, args)
                if name == "retire_agent" and parent.retire_failure == "drop":
                    self.close_connection = True
                    return
                if name == "retire_agent" and parent.retire_failure == "http-500":
                    self.send_response(500)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                    return
                reply = parent.answer(name, args)
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

    def answer(self, name: str, args: dict) -> dict:
        key = (args.get("project_key"), args.get("agent_name"))
        if name == "whois":
            if args.get("registration_token") and self.owners.get(key) == args["registration_token"]:
                return {"result": {"structuredContent": {"name": args["agent_name"]}}}
            return {"error": {"code": -32000, "message": "invalid registration_token"}}
        if name == "retire_agent":
            if self.retire_failure == "tool-error":
                return {"result": {"isError": True, "content": [
                    {"type": "text", "text": "Error calling tool 'retire_agent': busy"}]}}
            if self.owners.get(key) == args.get("registration_token"):
                del self.owners[key]
                return {"result": {"structuredContent": {"retired": True}}}
            return {"error": {"code": -32000, "message": "invalid registration_token"}}
        if name == "register_agent":
            returned = self.rename.get(args["name"], args["name"])
            self.owners[(args["project_key"], returned)] = args["registration_token"]
            return {"result": {"structuredContent": {"id": 7, "name": returned}}}
        return {"result": {"structuredContent": {"ok": True, "granted": [], "conflicts": []}}}

    def tools(self) -> list[str]:
        return [item["tool"] for item in self.requests]

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/mcp"

    def __enter__(self) -> "FakeMail":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()


def git(*args: str, cwd: pathlib.Path | None = None, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path: pathlib.Path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AGENTSTACK_", "GIT_")) and key not in {
               "HOME", "PROJECT_KEY", "AGENT_NAME", "PARENT_AGENT", "TMUX", "TMUX_PANE",
               "CHILD_REGISTRATION_TOKEN", "MCP_AGENT_MAIL_TOKEN", "MCP_URL", "BASH_ENV", "ENV"}}
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    runtime = tmp_path / "runtime"
    env.update(HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               PATH=f"{bindir}:{env['PATH']}",
               AGENTSTACK_RUNTIME_DIR=str(runtime),
               AGENTSTACK_HOOKS_DIR=str(HOOKS),
               AGENTSTACK_REGISTER_LIB=str(LIB),
               AGENTSTACK_HOME=str(tmp_path / "agentstack"),
               AGENTSTACK_MAIL_HTTP_BEARER_MODE="disabled",
               AGENTSTACK_MANAGED_AGENTS_FILE=str(runtime / "managed_agents.txt"),
               AGENTSTACK_TERMINAL="none",
               AGENTSTACK_MCP_PROXY=str(tmp_path / "missing-proxy"),
               PARENT_AGENT="Parent-Bohr",
               FAKE_TMUX_LOG=str(tmp_path / "tmux.log"),
               FAKE_TMUX_ALIVE=str(tmp_path / "tmux.alive"))
    repos = {}
    for name in ("alpha", "beta"):
        repo = tmp_path / name
        git("init", "-q", str(repo), env=env)
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "--allow-empty", "-qm", "init", cwd=repo, env=env)
        repos[name] = repo.resolve()
    linked = tmp_path / "alpha-linked"
    git("worktree", "add", "-q", "-b", "linked", str(linked), cwd=repos["alpha"], env=env)
    (bindir / "tmux").write_text(
        "#!/bin/bash\n"
        "{ printf 'CALL'; for arg in \"$@\"; do printf '\\034%s' \"$arg\"; done; printf '\\n'; } >> \"$FAKE_TMUX_LOG\"\n"
        "case \"${1:-}\" in\n"
        "  new-session) [[ \"${FAKE_TMUX_FAIL:-0}\" == 1 ]] && exit 1; : > \"$FAKE_TMUX_ALIVE\" ;;\n"
        "  capture-pane) printf '\\n❯ \\n' ;;\n"
        "  has-session) [[ -f \"$FAKE_TMUX_ALIVE\" ]] ;;\n"
        "  kill-session) rm -f \"$FAKE_TMUX_ALIVE\" ;;\n"
        "  display-message) printf 'Parent-Bohr\\n' ;;\n"
        "esac\n", encoding="utf-8")
    (bindir / "sleep").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (bindir / "claude").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    for tool in ("tmux", "sleep", "claude"):
        (bindir / tool).chmod(0o755)
    return {"env": env, "home": home, "bin": bindir, "runtime": runtime, "tmp": tmp_path,
            "alpha": repos["alpha"], "beta": repos["beta"], "linked": linked.resolve()}


def run(world, command, *, cwd, mail=None, **overrides):
    env = dict(world["env"])
    if mail is not None:
        env["AGENTSTACK_MCP_URL"] = mail.url
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return subprocess.run([str(part) for part in command], cwd=cwd, env=env,
                          text=True, capture_output=True, timeout=60)


def private(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def token_file(world) -> pathlib.Path:
    return world["runtime"] / f"agent_token_{CHILD}"


def state_file(world) -> pathlib.Path:
    return world["runtime"] / "child-agents" / f"{CHILD}.json"


def arrange_child(world, *, project, repository, work_dir, token=TOKEN):
    """Local artifacts a launched child leaves for its cleanup."""
    private(token_file(world), token)
    private(state_file(world), json.dumps({
        "agent_name": CHILD, "project_key": str(project), "registration_token": token,
        "repository_key": str(repository) if repository else None,
        "work_dir": str(work_dir)}))
    config = world["runtime"] / "child-agents" / f"{CHILD}.mcp.json"
    config.write_text("{}", encoding="utf-8")
    return [token_file(world), state_file(world), config]


def tmux_calls(world) -> str:
    log = pathlib.Path(world["env"]["FAKE_TMUX_LOG"])
    return log.read_text(encoding="utf-8") if log.exists() else ""


# --- cleanup --------------------------------------------------------------


def test_cleanup_in_its_own_repository_releases_retires_and_removes(world):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["linked"], mail=mail,
                     AGENTSTACK_PROJECT_KEY="/somewhere/stale")
    assert result.returncode == 0, result.stderr
    assert mail.tools() == ["whois", "release_file_reservations", "retire_agent"]
    assert {r["args"]["project_key"] for r in mail.requests} == {str(world["alpha"])}
    assert not any(path.exists() for path in files)
    assert TOKEN not in result.stdout + result.stderr


def test_cleanup_from_a_foreign_repository_touches_nothing(world):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        # A key naming the foreign repository does not help either.
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["beta"], mail=mail,
                     AGENTSTACK_PROJECT_KEY=world["beta"])
    assert result.returncode != 0
    assert mail.requests == []
    assert all(path.exists() for path in files)


def test_cleanup_of_a_legacy_record_from_a_foreign_repository_touches_nothing(world):
    # State written before the workspace tuple existed: only the directory
    # cleanup runs in can be checked against the recorded project.
    files = arrange_child(world, project=world["alpha"], repository=None, work_dir="")
    state = json.loads(state_file(world).read_text())
    del state["repository_key"], state["work_dir"]
    private(state_file(world), json.dumps(state))
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        foreign = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["beta"], mail=mail)
        assert foreign.returncode != 0
        assert mail.requests == []
        assert all(path.exists() for path in files)
        own = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail)
    assert own.returncode == 0, own.stderr
    assert not any(path.exists() for path in files)


def test_cleanup_with_a_contradictory_saved_workspace_touches_nothing(world):
    # Recorded project and repository say alpha, recorded directory says beta.
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["beta"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert mail.requests == []
    assert all(path.exists() for path in files)


def test_cleanup_with_state_for_another_agent_touches_nothing(world):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    state = json.loads(state_file(world).read_text())
    state["agent_name"] = "Other-Bohr"
    private(state_file(world), json.dumps(state))
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert mail.requests == []
    assert all(path.exists() for path in files)


def test_cleanup_with_a_same_name_foreign_token_only_asks_mail(world):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        # The name exists in alpha, but under another registration's token.
        mail.owners[(str(world["alpha"]), CHILD)] = "someone-elses-token"
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert mail.tools() == ["whois"]
    assert all(path.exists() for path in files)


def test_cleanup_refuses_disagreeing_credentials_before_mail(world):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail,
                     CHILD_REGISTRATION_TOKEN="token-of-a-previous-process")
    assert result.returncode != 0
    assert mail.requests == []
    assert all(path.exists() for path in files)


@pytest.mark.parametrize("moment", ["release_file_reservations", "retire_agent"])
def test_cleanup_keeps_credentials_replaced_during_mail_calls(world, moment):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN

        def replace(name, _args):
            if name == moment:
                private(token_file(world), "replacement-token")
                state = json.loads(state_file(world).read_text())
                state["registration_token"] = "replacement-token"
                private(state_file(world), json.dumps(state))

        mail.on_call = replace
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert all(path.exists() for path in files)
    assert token_file(world).read_text() == "replacement-token"
    expected = ["whois", "release_file_reservations"]
    if moment == "retire_agent":
        expected.append("retire_agent")
    assert mail.tools() == expected


@pytest.mark.parametrize("failure", ["tool-error", "http-500", "drop"])
def test_cleanup_keeps_the_owner_credential_unless_retirement_is_confirmed(world, failure):
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        mail.retire_failure = failure
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert mail.tools() == ["whois", "release_file_reservations", "retire_agent"]
    assert all(path.exists() for path in files), "the only way to retire later"
    assert token_file(world).read_text() == TOKEN


@pytest.mark.parametrize("bound, accepted", [("alpha", True), ("beta", False)])
def test_cleanup_logical_namespace_needs_matching_repository(world, bound, accepted):
    files = arrange_child(world, project="team-x", repository=world["alpha"],
                          work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[("team-x", CHILD)] = TOKEN
        result = run(world, ["/bin/bash", CLEANUP, CHILD], cwd=world["alpha"], mail=mail,
                     AGENTSTACK_PROJECT_KEY="team-x",
                     AGENTSTACK_PROJECT_REPOSITORY=world[bound])
    if accepted:
        assert result.returncode == 0, result.stderr
        assert {r["args"]["project_key"] for r in mail.requests} == {"team-x"}
        assert not any(path.exists() for path in files)
    else:
        assert result.returncode != 0
        assert mail.requests == []
        assert all(path.exists() for path in files)


# --- preregistration ------------------------------------------------------


def test_preregister_refuses_a_foreign_work_dir_before_mail(world):
    out = world["tmp"] / "handoff" / "token"
    with FakeMail() as mail:
        result = run(world, [PREREGISTER, "--project-key", world["alpha"],
                             "--work-dir", world["beta"], "--name", CHILD,
                             "--token-file-out", out], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert mail.requests == []
    assert not out.exists()
    assert not token_file(world).exists()


def test_preregister_never_replaces_same_name_local_credentials(world):
    private(token_file(world), "another-registration")
    out = world["tmp"] / "handoff" / "token"
    with FakeMail() as mail:
        result = run(world, [PREREGISTER, "--project-key", world["alpha"],
                             "--work-dir", world["alpha"], "--name", CHILD,
                             "--token-file-out", out], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert "register_agent" not in mail.tools()
    assert "ensure_project" not in mail.tools()
    assert token_file(world).read_text() == "another-registration"
    assert not out.exists()


def test_preregister_keeps_the_new_handoff_when_the_returned_name_collides(world):
    private(token_file(world), "another-registration")
    out = world["tmp"] / "handoff" / "token"
    with FakeMail() as mail:
        mail.rename["Calm-Bohr"] = CHILD
        result = run(world, [PREREGISTER, "--project-key", world["alpha"],
                             "--work-dir", world["alpha"], "--name", "Calm-Bohr",
                             "--token-file-out", out], cwd=world["alpha"], mail=mail)
    assert result.returncode != 0
    assert "register_agent" in mail.tools()
    assert token_file(world).read_text() == "another-registration"
    assert not state_file(world).exists()
    # The new registration stays recoverable through its one-shot handoff.
    assert out.read_text() == mail.owners[(str(world["alpha"]), CHILD)]
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    receipt = json.loads(out.with_name(out.name + ".binding.json").read_text())
    assert receipt["agent_name"] == CHILD
    assert receipt["project_key"] == str(world["alpha"])


def test_preregister_binds_a_linked_worktree_to_the_canonical_project(world):
    out = world["tmp"] / "handoff" / "token"
    spelled = f"{world['tmp']}/alpha/."  # a non-canonical spelling of the key
    with FakeMail() as mail:
        result = run(world, [PREREGISTER, "--project-key", spelled,
                             "--work-dir", world["linked"], "--name", CHILD,
                             "--token-file-out", out], cwd=world["home"], mail=mail)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == CHILD
    register = [r for r in mail.requests if r["tool"] == "register_agent"]
    assert register and register[0]["args"]["project_key"] == str(world["alpha"])
    binding = json.loads(out.with_name(out.name + ".binding.json").read_text())
    assert binding["agent_name"] == CHILD
    assert binding["project_key"] == str(world["alpha"])
    assert binding["repository_key"] == str(world["alpha"])
    assert binding["work_dir"] == str(world["linked"])


# --- pre-registered launch -------------------------------------------------


def handoff(world, *, token=TOKEN, binding=None) -> pathlib.Path:
    path = private(world["tmp"] / "handoffs" / "child-token", token)
    if binding is not None:
        private(path.with_name(path.name + ".binding.json"), json.dumps(binding))
    return path


def launch(world, mail, one_shot, work_dir, **overrides):
    overrides.setdefault("AGENTSTACK_PROJECT_KEY", world["alpha"])
    return run(world, ["/bin/bash", SPAWN, "--pre-registered", CHILD,
                       "--child-token-file", one_shot, "task text", work_dir],
               cwd=world["home"], mail=mail, **overrides)


def assert_untouched(world, mail, one_shot, token=TOKEN):
    assert not [r for r in mail.requests if r["tool"] != "whois"]
    assert "new-session" not in tmux_calls(world)
    assert one_shot.read_text() == token
    assert not token_file(world).exists() or token_file(world).read_text() != token
    assert not state_file(world).exists()


def test_preregistered_launch_in_a_foreign_workspace_has_no_side_effects(world):
    one_shot = handoff(world)
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["beta"])
    assert result.returncode != 0
    assert mail.requests == []
    assert_untouched(world, mail, one_shot)


def test_preregistered_launch_rejects_a_token_mail_does_not_accept(world):
    one_shot = handoff(world)
    with FakeMail() as mail:
        mail.owners[(str(world["beta"]), CHILD)] = TOKEN  # valid only elsewhere
        result = launch(world, mail, one_shot, world["alpha"])
    assert result.returncode != 0
    assert mail.tools() == ["whois"]
    assert_untouched(world, mail, one_shot)


@pytest.mark.parametrize("field, value", [
    ("agent_name", "Other-Bohr"), ("project_key", "/another/project"),
    ("repository_key", "/another/repository")])
def test_preregistered_launch_rejects_a_contradictory_receipt_before_mail(world, field, value):
    binding = {"agent_name": CHILD, "project_key": str(world["alpha"]),
               "program": "claude-code", "repository_key": str(world["alpha"])}
    binding[field] = value
    one_shot = handoff(world, binding=binding)
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["alpha"])
    assert result.returncode != 0
    assert mail.requests == []
    assert_untouched(world, mail, one_shot)


def test_launch_without_handoff_rejects_a_contradictory_saved_workspace(world):
    # A valid owner token, but the saved state records a directory in beta
    # while naming alpha as its project and repository.
    files = arrange_child(world, project=world["alpha"], repository=world["alpha"],
                          work_dir=world["beta"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", SPAWN, "--pre-registered", CHILD, "task", world["alpha"]],
                     cwd=world["home"], mail=mail, AGENTSTACK_PROJECT_KEY=world["alpha"])
    assert result.returncode != 0
    assert "records another workspace" in result.stderr
    assert mail.requests == []
    assert "new-session" not in tmux_calls(world)
    assert all(path.exists() for path in files)


def test_launch_without_handoff_accepts_a_saved_workspace_of_the_same_repository(world):
    arrange_child(world, project=world["alpha"], repository=world["alpha"],
                  work_dir=world["alpha"])
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", SPAWN, "--pre-registered", CHILD, "task", world["linked"]],
                     cwd=world["home"], mail=mail, AGENTSTACK_PROJECT_KEY=world["alpha"])
    assert result.returncode == 0, result.stderr
    assert mail.tools() == ["whois"]
    assert "new-session" in tmux_calls(world)


def test_handoff_rejects_saved_state_for_another_agent(world):
    private(state_file(world), json.dumps({
        "agent_name": "Other-Bohr", "project_key": str(world["alpha"]),
        "registration_token": TOKEN}))
    one_shot = handoff(world)
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["alpha"])
    assert result.returncode != 0
    assert one_shot.read_text() == TOKEN
    assert json.loads(state_file(world).read_text())["agent_name"] == "Other-Bohr"
    assert "new-session" not in tmux_calls(world)


def test_preregistered_launch_never_replaces_another_local_credential(world):
    private(token_file(world), "another-registration")
    one_shot = handoff(world)
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["alpha"])
    assert result.returncode != 0
    assert token_file(world).read_text() == "another-registration"
    assert one_shot.read_text() == TOKEN
    assert "new-session" not in tmux_calls(world)


@pytest.mark.parametrize("bound, accepted", [("alpha", True), ("beta", False)])
def test_preregistered_logical_namespace_needs_matching_repository(world, bound, accepted):
    one_shot = handoff(world)
    with FakeMail() as mail:
        mail.owners[("team-x", CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["alpha"],
                        AGENTSTACK_PROJECT_KEY="team-x", PROJECT_KEY="team-x",
                        AGENTSTACK_PROJECT_REPOSITORY=world[bound])
    if accepted:
        assert result.returncode == 0, result.stderr
        assert mail.requests and {r["args"]["project_key"] for r in mail.requests} == {"team-x"}
    else:
        assert result.returncode != 0
        assert mail.requests == []
        assert_untouched(world, mail, one_shot)


def test_preregistered_launch_in_a_linked_worktree_carries_the_whole_context(world):
    binding = {"agent_name": CHILD, "project_key": str(world["alpha"]),
               "program": "claude-code", "repository_key": str(world["alpha"]),
               "work_dir": str(world["alpha"])}
    one_shot = handoff(world, binding=binding)
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["linked"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == CHILD
    assert mail.tools() == ["whois"]
    session = next(line for line in tmux_calls(world).split("\n") if "\x1cnew-session" in line)
    fields = session.split("\x1c")
    for expected in (f"AGENTSTACK_PROJECT_KEY={world['alpha']}",
                     f"AGENTSTACK_PROJECT_REPOSITORY={world['alpha']}",
                     f"AGENTSTACK_PROJECT_WORK_DIR={world['linked']}",
                     f"AGENTSTACK_PROTECTED_ROOTS={world['linked']}"):
        assert expected in fields, expected
    state = json.loads(state_file(world).read_text())
    assert state["project_key"] == str(world["alpha"])
    assert state["repository_key"] == str(world["alpha"])
    assert token_file(world).read_text() == TOKEN
    assert not one_shot.exists(), "a successful launch consumes the handoff"
    assert not one_shot.with_name(one_shot.name + ".binding.json").exists()
    assert TOKEN not in result.stdout + result.stderr + tmux_calls(world)


def test_failed_preregistered_launch_keeps_the_handoff_and_removes_only_its_copies(world):
    one_shot = handoff(world)
    with FakeMail() as mail:
        mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = launch(world, mail, one_shot, world["alpha"], FAKE_TMUX_FAIL=1)
    assert result.returncode != 0
    assert one_shot.read_text() == TOKEN, "the handoff is the recovery credential"
    assert not token_file(world).exists()
    assert not state_file(world).exists()


# --- direct launch ----------------------------------------------------------


def test_direct_launch_in_a_foreign_workspace_never_contacts_mail(world):
    with FakeMail() as mail:
        result = run(world, ["/bin/bash", SPAWN, "--unsafe-no-resources", "task", world["beta"]],
                     cwd=world["home"], mail=mail, AGENTSTACK_PROJECT_KEY=world["alpha"])
    assert result.returncode != 0
    assert mail.requests == []
    assert "new-session" not in tmux_calls(world)
    assert not world["runtime"].exists() or not list(world["runtime"].glob("agent_token_*"))


# --- Gemini -------------------------------------------------------------------


def gemini_home(world) -> pathlib.Path:
    home = world["tmp"] / "agentstack"
    (home / "bin" / "lib").mkdir(parents=True, exist_ok=True)
    for lib in ("agentstack-register.sh", "agentstack-scientists.sh"):
        shutil.copy2(ROOT / "bin" / "lib" / lib, home / "bin" / "lib" / lib)
    shutil.copytree(HOOKS, home / "hooks", dirs_exist_ok=True)
    log = world["tmp"] / "gemini-helpers.log"
    for helper in ("agentstack-gemini-child-mail", "agentstack-gemini-stream",
                   "agentstack-gemini-mcp", "agentstack-preregister-child"):
        path = home / "bin" / helper
        # Invoked both directly and through the selected Python.
        path.write_text("#!/usr/bin/env python3\nimport sys\n"
                        f"with open({str(log)!r}, 'a') as handle:\n"
                        f"    handle.write(' '.join([{helper!r}, *sys.argv[1:]]) + '\\n')\n",
                        encoding="utf-8")
        path.chmod(0o755)
    agy = world["bin"] / "agy"
    agy.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    agy.chmod(0o755)
    return home


def gemini_untouched(world, one_shot, home):
    log = world["tmp"] / "gemini-helpers.log"
    assert not log.exists(), log.read_text()
    assert "new-session" not in tmux_calls(world)
    assert one_shot.read_text() == TOKEN
    assert not token_file(world).exists()
    assert not (world["tmp"] / "worktrees").exists() or not any((world["tmp"] / "worktrees").iterdir())


@pytest.mark.parametrize("case", ["foreign-workspace", "token-not-accepted"])
def test_gemini_preregistered_refusals_have_no_side_effects(world, case):
    home = gemini_home(world)
    one_shot = handoff(world)
    task = private(world["tmp"] / "task.md", "task")
    work_dir = world["beta"] if case == "foreign-workspace" else world["alpha"]
    with FakeMail() as mail:
        if case == "foreign-workspace":
            mail.owners[(str(world["alpha"]), CHILD)] = TOKEN
        result = run(world, ["/bin/bash", home / "hooks" / "spawn_gemini_preregistered.sh",
                             "--pre-registered", CHILD, "--child-token-file", one_shot,
                             "--model", "gemini-3.8-flash-high", "task", work_dir],
                     cwd=world["home"], mail=mail,
                     AGENTSTACK_HOME=home, AGENTSTACK_HOOKS_DIR=home / "hooks",
                     AGENTSTACK_REGISTER_LIB=None,
                     AGENTSTACK_PROJECT_KEY=world["alpha"],
                     AGENTSTACK_GEMINI_RESOURCES="src/**",
                     AGENTSTACK_GEMINI_TASK_FILE=task,
                     AGENTSTACK_WORKTREE_ROOT=world["tmp"] / "worktrees")
    assert result.returncode != 0
    assert mail.tools() == ([] if case == "foreign-workspace" else ["whois"])
    gemini_untouched(world, one_shot, home)


def test_gemini_child_keeps_the_handoff_of_a_failed_preregistration(world):
    home = gemini_home(world)
    (home / "bin" / "agentstack-preregister-child").write_text(
        "#!/usr/bin/env python3\nimport os, sys\n"
        "out = sys.argv[sys.argv.index('--token-file-out') + 1]\n"
        "fd = os.open(out, os.O_WRONLY | os.O_CREAT, 0o600)\n"
        "os.write(fd, b'recovery-token')\nos.close(fd)\nsys.exit(1)\n",
        encoding="utf-8")
    with FakeMail() as mail:
        result = run(world, ["/bin/bash", home / "hooks" / "spawn_gemini_child.sh",
                             "--resources", "src/**", "task", world["alpha"]],
                     cwd=world["home"], mail=mail,
                     AGENTSTACK_HOME=home, AGENTSTACK_HOOKS_DIR=home / "hooks",
                     AGENTSTACK_REGISTER_LIB=None,
                     AGENTSTACK_PROJECT_KEY=world["alpha"],
                     AGENTSTACK_WORKTREE_ROOT=world["tmp"] / "worktrees")
    assert result.returncode != 0
    assert "kept the child registration handoff" in result.stderr
    kept = list(world["runtime"].glob("gemini-preregister-*.token"))
    assert [path.read_text() for path in kept] == ["recovery-token"]
    assert "new-session" not in tmux_calls(world)


def test_gemini_child_in_a_foreign_workspace_never_preregisters(world):
    home = gemini_home(world)
    with FakeMail() as mail:
        result = run(world, ["/bin/bash", home / "hooks" / "spawn_gemini_child.sh",
                             "--resources", "src/**", "task", world["beta"]],
                     cwd=world["home"], mail=mail,
                     AGENTSTACK_HOME=home, AGENTSTACK_HOOKS_DIR=home / "hooks",
                     AGENTSTACK_REGISTER_LIB=None,
                     AGENTSTACK_PROJECT_KEY=world["alpha"],
                     AGENTSTACK_WORKTREE_ROOT=world["tmp"] / "worktrees")
    assert result.returncode != 0
    assert "not valid for work directory" in result.stderr
    assert mail.requests == []
    assert not (world["tmp"] / "gemini-helpers.log").exists()
    assert "new-session" not in tmux_calls(world)
