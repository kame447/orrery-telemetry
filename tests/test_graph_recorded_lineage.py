"""Only retained launch identities, not conversations, restore missing spawn edges."""
from __future__ import annotations

import json
import sqlite3

import pytest

from dashboard import graph_data

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


def first_high_message(database):
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO messages VALUES (1, 1, 1, ?, 'high')", ((BASE + 120) * 1_000_000,))
        connection.execute("INSERT INTO message_recipients VALUES (1, 2, 'to')")


@pytest.fixture(params=["dsh", "hermes"])
def native_recorded(recorded, monkeypatch, request):
    database, path, state = recorded
    program = request.param
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE agents SET program = ? WHERE id = 2", (program,))
    directory = path.parent / "QuietChild.harness" / ("a" * 32)
    directory.mkdir(parents=True)
    directory = directory.resolve()
    state.update(program=program, provider=program, harness=program,
                 harness_backend="strata", harness_state_dir=str(directory))
    state.pop("parent_agent")
    save(path, state)
    launch = {
        "agent_name": "QuietChild", "program": program, "project_key": PROJECT,
        "backend_id": "strata", "state_dir": str(directory),
        "runtime_dir": str(path.parent.parent.resolve()), "cwd": str(directory),
        "parent_agent": None,
    }
    save(directory / "input.json", launch)
    save(directory / "native-session.json", {
        "harness": program, "agent_name": "QuietChild", "native_session_id": "native-session",
        "created_at": "2026-11-27T00:00:00+00:00",
    })
    first_high_message(database)
    return database, path, state, directory, launch


def test_verified_native_standalone_stays_parentless_after_high_mail(native_recorded, monkeypatch):
    database, _path, _state, _directory, _launch = native_recorded
    graph = _build(monkeypatch, database, {})
    assert graph["spawn"] == []
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [("Parent", "QuietChild")]


@pytest.mark.parametrize("invalid", [
    "missing_parent", "foreign_project", "foreign_program", "foreign_agent", "symlink", "unselected_generation",
    "unavailable_validator", "foreign_runtime", "foreign_state_dir", "foreign_backend", "foreign_kind",
    "public_input", "oversized_input", "directory_symlink", "malformed_input",
])
def test_unverified_native_launch_does_not_claim_standalone(native_recorded, monkeypatch, invalid):
    database, path, state, directory, launch = native_recorded
    if invalid == "missing_parent":
        launch.pop("parent_agent")
    elif invalid == "foreign_project":
        launch["project_key"] = "/another-project"
    elif invalid == "foreign_program":
        launch["program"] = "claude-code"
    elif invalid == "foreign_agent":
        launch["agent_name"] = "DifferentChild"
    elif invalid == "unselected_generation":
        current = directory.with_name("b" * 32)
        current.mkdir()
        save(path, {**state, "harness_state_dir": str(current)})
    elif invalid == "unavailable_validator":
        def unavailable():
            raise ValueError("unavailable")
        monkeypatch.setattr(graph_data, "_lineage_native_connections", unavailable)
    elif invalid == "foreign_runtime":
        launch["runtime_dir"] = str(directory / "other-runtime")
    elif invalid == "foreign_state_dir":
        launch["state_dir"] = str(directory.with_name("b" * 32))
    elif invalid == "foreign_backend":
        launch["backend_id"] = "deepseek"
    elif invalid == "foreign_kind":
        launch["backend_kind"] = "deepseek"
    elif invalid == "oversized_input":
        launch["padding"] = "x" * 65537
    save(directory / "input.json", launch)
    if invalid == "public_input":
        (directory / "input.json").chmod(0o644)
    elif invalid == "directory_symlink":
        target = directory.with_name("b" * 32)
        directory.rename(target)
        directory.symlink_to(target, target_is_directory=True)
    elif invalid == "malformed_input":
        (directory / "input.json").write_text('{"nested":' + '[' * 10000 + '0' + ']' * 10000 + '}')
    if invalid == "symlink":
        target = directory / "saved-input.json"
        (directory / "input.json").rename(target)
        (directory / "input.json").symlink_to(target)
    assert _build(monkeypatch, database, {})["spawn"] == [
        {"source": "Parent", "target": "QuietChild", "type": "spawn"}
    ]


def test_native_recorded_parent_is_not_erased_by_standalone_metadata(native_recorded, monkeypatch):
    database, path, state, _directory, _launch = native_recorded
    save(path, {**state, "parent_agent": "Parent", "codex_standalone": True})
    assert _build(monkeypatch, database, {})["spawn"] == [
        {"source": "Parent", "target": "QuietChild", "type": "spawn"}
    ]


@pytest.mark.parametrize("program,provider", [("claude-code", "claude"), ("dsh", "dsh"), ("hermes", "hermes")])
def test_missing_parent_without_launch_evidence_is_not_standalone(recorded, monkeypatch, program, provider):
    database, path, state = recorded
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE agents SET program = ? WHERE id = 2", (program,))
    state.update(program=program, provider=provider, codex_standalone=True)
    state.pop("parent_agent")
    save(path, state)
    first_high_message(database)
    assert _build(monkeypatch, database, {})["spawn"] == [
        {"source": "Parent", "target": "QuietChild", "type": "spawn"}
    ]



@pytest.mark.parametrize("unavailable", ["cwd_removed", "native_session_missing", "native_session_invalid"])
def test_native_standalone_lineage_does_not_require_resume_material(native_recorded, monkeypatch, unavailable):
    database, path, _state, directory, launch = native_recorded
    if unavailable == "cwd_removed":
        cwd = directory / "former-cwd"
        cwd.mkdir()
        launch["cwd"] = str(cwd)
        save(directory / "input.json", launch)
        assert _build(monkeypatch, database, {})["spawn"] == []
        cwd.rmdir()
    elif unavailable == "native_session_missing":
        (directory / "native-session.json").unlink()
    else:
        (directory / "native-session.json").write_text("invalid session metadata")
    state_before = path.read_bytes()
    input_before = (directory / "input.json").read_bytes()
    assert _build(monkeypatch, database, {})["spawn"] == []
    assert path.read_bytes() == state_before
    assert (directory / "input.json").read_bytes() == input_before


def test_native_standalone_reader_does_not_execute_resume_helper(native_recorded, monkeypatch):
    database, _path, _state, directory, _launch = native_recorded
    helper = directory / "broken-resume.py"
    helper.write_text("raise RuntimeError('resume helper must not run')\n")
    monkeypatch.setenv("AGENTSTACK_CHILD_RESUME_HELPER", str(helper))
    assert _build(monkeypatch, database, {})["spawn"] == []


def test_native_standalone_keeps_named_connection_identity(native_recorded, monkeypatch):
    database, path, state, directory, launch = native_recorded
    state.update(harness_backend="strata-epyc", harness_backend_kind="strata")
    launch.update(backend_id="strata-epyc", backend_kind="strata")
    save(path, state)
    save(directory / "input.json", launch)
    assert _build(monkeypatch, database, {})["spawn"] == []
