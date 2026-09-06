from types import SimpleNamespace

from dashboard import server


def _result(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def _codex_tree(live: bool):
    root = 70705
    commands = {
        root: (
            "zsh -lc 'exec env -u OPENAI_API_KEY "
            "/Applications/ChatGPT.app/Contents/Resources/codex -C /tmp'"
        ),
    }
    children = {root: []}
    if live:
        commands[70713] = (
            "node /Applications/ChatGPT.app/Contents/Resources/codex -C /tmp"
        )
        children[root].append(70713)
    return root, (commands, children)


def _build_codex_row(monkeypatch, *, live: bool):
    root, tree = _codex_tree(live)
    monkeypatch.setattr(
        server,
        "tmux_state",
        lambda: {
            "MintBoltzmann": {
                "name": "MintBoltzmann",
                "created": 100,
                "session_id": "$1",
                "activity": 200,
                "attached": False,
                "client_tty": None,
                "cmd": "zsh",
                "pane_pid": root,
                "title": "",
            },
        },
    )
    monkeypatch.setattr(
        server,
        "agentmail_state",
        lambda: ({
            "MintBoltzmann": {
                "model": "GPT 5.6",
                "model_raw": "gpt-5.6-sol",
                "program": "codex-cli",
                "task": "test",
                "last_active": 150,
            },
        }, {}),
    )
    monkeypatch.setattr(server, "_process_tree_snapshot", lambda: tree)
    monkeypatch.setattr(server, "_codex_app_runtimes", lambda: {})
    monkeypatch.setattr(server, "_deliverables_index", lambda: {})
    monkeypatch.setattr(server, "_retired_names", lambda _project: set())
    monkeypatch.setattr(server, "_name_substitutions", lambda: {})
    monkeypatch.setattr(server, "_agent_runtime", lambda *_args: {})
    monkeypatch.setattr(server, "_project_key", lambda: "")
    return server.build_agents()[0]


def test_codex_process_tree_detects_wrapped_cli():
    root, tree = _codex_tree(True)
    assert server._codex_process_alive(root, tree) is True


def test_codex_process_tree_rejects_shell_husk_even_if_launcher_mentions_codex():
    root, tree = _codex_tree(False)
    assert "codex" in tree[0][root]
    assert server._codex_process_alive(root, tree) is False


def test_deck_keeps_live_wrapped_codex_online(monkeypatch):
    row = _build_codex_row(monkeypatch, live=True)
    assert row["category"] == "agent"
    assert row["running"] is True


def test_deck_marks_finished_codex_shell_husk_offline(monkeypatch):
    row = _build_codex_row(monkeypatch, live=False)
    assert row["category"] == "finished"
    assert row["running"] is False


def test_graph_marks_finished_codex_shell_husk_offline(monkeypatch):
    root, tree = _codex_tree(False)
    monkeypatch.setattr(
        server,
        "_raw_graph",
        lambda: {
            "nodes": [{
                "name": "MintBoltzmann",
                "program": "codex-cli",
                "model": "gpt-5.6-sol",
                "last_active": 200,
                "retired": False,
            }],
            "edges": [],
            "spawn": [],
        },
    )
    monkeypatch.setattr(
        server,
        "tmux_state",
        lambda: {
            "MintBoltzmann": {
                "name": "MintBoltzmann",
                "created": 100,
                "session_id": "$1",
                "activity": 200,
                "attached": False,
                "client_tty": None,
                "cmd": "zsh",
                "pane_pid": root,
                "title": "",
            },
        },
    )
    monkeypatch.setattr(server, "_process_tree_snapshot", lambda: tree)
    monkeypatch.setattr(server, "_codex_app_runtimes", lambda: {})
    monkeypatch.setattr(server, "_deliverables_index", lambda: {})
    monkeypatch.setattr(server, "_annotations", lambda: {})
    monkeypatch.setattr(server, "_name_substitutions", lambda: {})
    monkeypatch.setattr(server, "_agent_runtime", lambda *_args: {})

    payload = server.graph_payload(4, True)
    node = payload["nodes"][0]
    assert node["running"] is False
    assert node["state"] == "finished"


def test_active_codex_wrapped_by_zsh_gets_slash_exit(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_agents",
        lambda: [{
            "name": "AshGuericke",
            "category": "agent",
            "running": True,
            "attached": False,
        }],
    )
    monkeypatch.setattr(server, "_has_session", lambda _session: True)
    monkeypatch.setattr(server.time, "sleep", lambda _seconds: None)

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["tmux", "display-message", "-t"]:
            return _result(stdout="zsh\n")
        return _result()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.do_exit("AshGuericke")

    assert result == {
        "ok": True,
        "session": "AshGuericke",
        "actions": ["exit-sent"],
    }
    assert ["tmux", "send-keys", "-t", "AshGuericke", "-l", "/exit"] in calls
    assert ["tmux", "send-keys", "-t", "AshGuericke", "C-m"] in calls
    assert ["tmux", "send-keys", "-t", "AshGuericke", "exit", "Enter"] not in calls


def test_finished_shell_husk_still_gets_shell_exit(monkeypatch):
    monkeypatch.setattr(
        server,
        "build_agents",
        lambda: [{
            "name": "FinishedAgent",
            "category": "finished",
            "running": False,
            "attached": False,
        }],
    )
    monkeypatch.setattr(server, "_has_session", lambda _session: True)

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["tmux", "display-message", "-t"]:
            return _result(stdout="zsh\n")
        return _result()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server.do_exit("FinishedAgent")

    assert result == {
        "ok": True,
        "session": "FinishedAgent",
        "actions": ["shell-exit-sent", "zombie-pane:zsh"],
    }
    assert ["tmux", "send-keys", "-t", "FinishedAgent", "exit", "Enter"] in calls
    assert ["tmux", "send-keys", "-t", "FinishedAgent", "-l", "/exit"] not in calls



def test_tmux_state_accepts_legacy_four_field_fake_pane_rows(monkeypatch):
    sep = server.SEP

    def fake_tmux(args):
        if args[0] == "list-sessions":
            return sep.join(("DemoAgent", "100", "200", "$1")) + "\n"
        if args[0] == "list-panes":
            # dashboard-demo.py / older fake tmux adapters ignore the requested
            # pane_pid field and still return the historical four-field shape.
            return sep.join(("DemoAgent", "11", "zsh", "demo title")) + "\n"
        if args[0] == "list-clients":
            return ""
        return ""

    monkeypatch.setattr(server, "_tmux", fake_tmux)
    monkeypatch.setattr(server, "_prune_runtime_cache", lambda _sessions: None)

    state = server.tmux_state()["DemoAgent"]
    assert state["cmd"] == "zsh"
    assert state["title"] == "demo title"
    assert state["pane_pid"] == 0
