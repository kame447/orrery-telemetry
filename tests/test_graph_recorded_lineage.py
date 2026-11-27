"""Only retained launch identities, not conversations, restore missing spawn edges."""
from __future__ import annotations

import json
import sqlite3

import pytest

from test_graph_live_lineage import BASE, PROJECT, _build, _make_db


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    directory = runtime / "child-agents"
    directory.mkdir(parents=True)
    monkeypatch.setenv("AGENTSTACK_RUNTIME_DIR", str(runtime))
    database = _make_db(tmp_path / "mail.sqlite3")
    state = {
        "schema_version": 1,
        "agent_id": 2,
        "agent_name": "QuietChild",
        "project_key": PROJECT,
        "program": "codex-cli",
        "provider": "codex",
        "launch_origin": "child",
        "parent_agent": "Parent",
        "registration_token": "fixture-private-token",
        "retired_at": "2026-11-26T00:00:00Z",
        "resume_in_progress_at": "2026-11-27T00:00:00Z",
    }
    path = directory / "QuietChild.json"
    return database, path, state


def save(path, state):
    path.write_text(json.dumps(state))
    path.chmod(0o600)


def test_resumed_child_keeps_recorded_parent_without_tmux_variable_or_mail(recorded, monkeypatch):
    database, path, state = recorded
    save(path, state)
    graph = _build(monkeypatch, database, {})
    assert graph["spawn"] == [{"source": "Parent", "target": "QuietChild", "type": "spawn"}]
    assert "fixture-private-token" not in json.dumps(graph)
    assert "registration_token" not in json.dumps(graph)


@pytest.mark.parametrize("key,value", [
    ("agent_id", 999),
    ("agent_id", True),
    ("agent_name", "DifferentChild"),
    ("project_key", "/another-project"),
    ("program", "claude-code"),
    ("program", []),
    ("provider", "claude"),
    ("schema_version", True),
    ("parent_agent", "QuietChild"),
    ("parent_agent", "../Parent"),
    ("tools_change_generation", "pending-change"),
])
def test_unverified_or_self_parent_is_not_attached(recorded, monkeypatch, key, value):
    database, path, state = recorded
    save(path, {**state, key: value})
    assert _build(monkeypatch, database, {})["spawn"] == []


@pytest.mark.parametrize("unsafe", ["public", "symlink", "oversized", "pending", "directory-symlink"])
def test_unsafe_retained_metadata_is_ignored(recorded, monkeypatch, unsafe):
    database, path, state = recorded
    save(path, state)
    if unsafe == "public":
        path.chmod(0o644)
    elif unsafe == "symlink":
        target = path.with_suffix(".saved")
        path.rename(target)
        path.symlink_to(target)
    elif unsafe == "oversized":
        save(path, {**state, "padding": "x" * 65537})
    elif unsafe == "pending":
        path.with_name(".QuietChild.registration-pending.json").write_text("{}")
    else:
        directory = path.parent
        target = directory.with_name("saved-children")
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
    assert _build(monkeypatch, database, {})["spawn"] == []


@pytest.mark.parametrize("standalone", [{"codex_standalone": True}, {"launch_origin": "standalone"}])
def test_explicit_standalone_does_not_acquire_a_parent_from_mail(recorded, monkeypatch, standalone):
    database, path, state = recorded
    save(path, {**state, **standalone})
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO messages VALUES (1, 1, 1, ?, 'high')", ((BASE + 120) * 1_000_000,))
        connection.execute("INSERT INTO message_recipients VALUES (1, 2, 'to')")
    assert _build(monkeypatch, database, {})["spawn"] == []


def test_current_session_parent_overrides_retained_parent(recorded, monkeypatch):
    database, path, state = recorded
    save(path, state)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO agents VALUES (3, 1, 'OtherParent', 'model', 'codex', 'task', ?, ?, NULL)",
            ((BASE + 600) * 1_000_000, (BASE - 10) * 1_000_000),
        )
    assert _build(monkeypatch, database, {"QuietChild": "OtherParent"})["spawn"] == [
        {"source": "OtherParent", "target": "QuietChild", "type": "spawn"}
    ]


def test_normal_mail_without_launch_record_stays_communication(recorded, monkeypatch):
    database, _path, _state = recorded
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO messages VALUES (1, 1, 1, ?, 'normal')", ((BASE + 120) * 1_000_000,))
        connection.execute("INSERT INTO message_recipients VALUES (1, 2, 'to')")
    graph = _build(monkeypatch, database, {})
    assert graph["spawn"] == []
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [("Parent", "QuietChild")]
