"""Claude liveness comes from the process tree, like Codex after #19.

On macOS every Claude Code session is `zsh > claude` (the pane leader is the
shell), so pane_current_command says "zsh" whether Claude is alive or already
gone, and the title glyph is the only other signal. A live Claude whose title
has no glyph was shown as FINISHED and EXIT typed a bare `exit` into it
(2026-09-08). With the shared `ps` snapshot the classification no longer
depends on the title.
"""
from types import SimpleNamespace

from dashboard import server


ROOT = 61000


def _tree(child_name: str | None):
    names = {ROOT: "zsh"}
    children = {ROOT: []}
    if child_name is not None:
        names[61010] = child_name
        children[ROOT].append(61010)
    return names, children


def _patch_agent_inputs(monkeypatch, process_tree, title="notes-vault"):
    monkeypatch.setattr(
        server,
        "tmux_state",
        lambda: {
            "QuietHooke": {
                "name": "QuietHooke",
                "created": 100,
                "session_id": "$7",
                "activity": 200,
                "attached": False,
                "client_tty": None,
                "cmd": "zsh",
                "pane_pid": ROOT,
                "title": title,
            },
        },
    )
    monkeypatch.setattr(
        server,
        "agentmail_state",
        lambda: ({
            "QuietHooke": {
                "model": "Sonnet 5",
                "model_raw": "claude-sonnet-5",
                "program": "claude-code",
                "task": "test",
                "last_active": 150,
            },
        }, {}),
    )
    monkeypatch.setattr(server, "_process_tree_snapshot", lambda: process_tree)
    monkeypatch.setattr(server, "_codex_app_runtimes", lambda: {})
    monkeypatch.setattr(server, "_deliverables_index", lambda: {})
    monkeypatch.setattr(server, "_retired_names", lambda _project: set())
    monkeypatch.setattr(server, "_name_substitutions", lambda: {})
    monkeypatch.setattr(server, "_agent_runtime", lambda *_args: {})
    monkeypatch.setattr(server, "_project_key", lambda: "")


def test_live_claude_without_a_title_glyph_is_online(monkeypatch):
    _patch_agent_inputs(monkeypatch, _tree("claude"))
    row = server.build_agents()[0]
    assert row["category"] == "agent"
    assert row["running"] is True


def test_live_claude_named_by_its_version_is_online(monkeypatch):
    # Claude Code 2.1.26x native binaries appear as "2.1.263" in ps.
    _patch_agent_inputs(monkeypatch, _tree("2.1.263"))
    row = server.build_agents()[0]
    assert row["category"] == "agent"
    assert row["running"] is True


def test_claude_husk_is_finished_even_with_a_glyph_in_the_title(monkeypatch):
    # The title is whatever the last program left; the tree is what is true.
    _patch_agent_inputs(monkeypatch, _tree(None), title="✳ leftover title")
    row = server.build_agents()[0]
    assert row["category"] == "finished"
    assert row["running"] is False


def test_unmeasurable_tree_keeps_the_title_glyph_reading(monkeypatch):
    _patch_agent_inputs(monkeypatch, None)
    assert server.build_agents()[0]["category"] == "finished"
    _patch_agent_inputs(monkeypatch, None, title="✳ working")
    assert server.build_agents()[0]["category"] == "agent"


def test_graph_uses_the_same_claude_liveness(monkeypatch):
    _patch_agent_inputs(monkeypatch, _tree("claude"))
    monkeypatch.setattr(
        server,
        "_raw_graph",
        lambda: {
            "nodes": [{
                "name": "QuietHooke",
                "program": "claude-code",
                "model": "claude-sonnet-5",
                "last_active": 200,
                "retired": False,
            }],
            "edges": [],
            "spawn": [],
        },
    )
    monkeypatch.setattr(server, "_annotations", lambda: {})
    node = server.graph_payload(4, True)["nodes"][0]
    assert node["running"] is True
    assert node["state"] != "finished"


def test_agent_process_name_is_program_specific():
    assert server._is_agent_process_name("codex", "codex-cli")
    assert not server._is_agent_process_name("claude", "codex-cli")
    assert server._is_agent_process_name("claude", "claude-code")
    assert server._is_agent_process_name("/Applications/Claude.app/Contents/MacOS/claude", "claude-code")
    assert server._is_agent_process_name("2.1.263", "claude-code")
    assert not server._is_agent_process_name("codex", "claude-code")
    assert server._agent_process_alive(ROOT, _tree("claude"), "gemini") is None
