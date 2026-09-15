"""Dashboard reads use the selected repository, including caches and fallbacks."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashboard import server


def _git(*args: str) -> None:
    env = {key: value for key, value in os.environ.items()
           if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"}}
    subprocess.run(["git", *args], env=env, check=True, capture_output=True, text=True)


@pytest.fixture
def projects(tmp_path: Path, monkeypatch):
    root = tmp_path.resolve()
    main, linked, other = (root / name for name in ("main", "linked", "other"))
    for path in (main, other):
        _git("init", "-q", str(path))
        _git("-C", str(path), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
             "commit", "--allow-empty", "-qm", "fixture")
    _git("-C", str(main), "worktree", "add", "--detach", str(linked), "HEAD")
    for key in ("AGENTSTACK_PROJECT_KEY", "PROJECT_KEY", "AGENTSTACK_PROJECT_CONTEXT",
                "AGENTSTACK_PROJECT_REPOSITORY", "AGENTSTACK_PROJECT_WORK_DIR",
                "AGENTSTACK_PROJECT_CONTEXT_JSON"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(server, "PROJECT_KEY", str(linked))
    monkeypatch.setattr(server, "VAULT", "")
    monkeypatch.setattr(server, "_PROJECT_KEY_CACHE", {"signature": None, "value": ""})
    monkeypatch.setattr(server, "_TPATH_CACHE", {})
    monkeypatch.setattr(server, "_TPATH_OWNER", {})
    monkeypatch.setattr(server, "_HIST_CACHE", {})
    monkeypatch.setattr(server, "_CAPP_CACHE", {"ts": 0.0, "project_key": None, "map": {}})
    monkeypatch.setattr(server, "_MAIL_HEALTH_CACHE", {"ts": 0.0, "project_key": None, "data": None})
    monkeypatch.setattr(server, "RUNTIME_DIR", str(root / "runtime"))
    monkeypatch.setattr(server, "SESSION_INDEX_DIR", str(root / "index"))
    db = root / "mail.sqlite3"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    inception = now - timedelta(hours=1)
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE projects (id INTEGER PRIMARY KEY, human_key TEXT);
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT, program TEXT,
                model TEXT, task_description TEXT, inception_ts TEXT, last_active_ts TEXT,
                retired_at TEXT);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, project_id INTEGER, sender_id INTEGER,
                subject TEXT, body_md TEXT, created_ts TEXT, importance TEXT,
                thread_id TEXT, ack_required INTEGER, topic TEXT);
            CREATE TABLE message_recipients (
                message_id INTEGER, agent_id INTEGER, kind TEXT, read_ts TEXT, ack_ts TEXT);
        """)
        for project_id, key, marker, offset in ((1, main, "A", 0), (2, other, "B", 10)):
            con.execute("INSERT INTO projects VALUES (?, ?)", (project_id, str(key)))
            first = 2 * project_id - 1
            active = (now - timedelta(seconds=120 - offset)).isoformat()
            for agent_id, name in ((first, "SharedAgent"), (first + 1, "PeerAgent")):
                con.execute("INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                            (agent_id, project_id, name, "claude-code", "model-" + marker,
                             "task-" + marker, (inception + timedelta(seconds=offset)).isoformat(), active))
            for number, sender, recipient in ((1, first + 1, first), (2, first, first + 1)):
                message_id = 1000 * project_id + number
                con.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                            (message_id, project_id, sender, marker + "-subject-" + str(number),
                             marker + "-body", active, "high", "thread-" + marker, 1))
                con.execute("INSERT INTO message_recipients VALUES (?, ?, 'to', NULL, NULL)",
                            (message_id, recipient))
    monkeypatch.setattr(server, "DB_PATH", str(db))
    monkeypatch.setattr(server, "_RETIRED_AT_CACHE", {})
    return SimpleNamespace(main=main, linked=linked, other=other, db=db, now=now, inception=inception)


@pytest.mark.parametrize("read", ["mail", "edge", "since", "window"])
def test_mail_reads_resolve_linked_worktree_before_same_name_lookup(projects, read):
    assert server._project_key() == str(projects.main)
    if read == "mail":
        agents, instructions = server.agentmail_state()
        assert agents["SharedAgent"]["task"] == "task-A"
        assert instructions["SharedAgent"]["subject"] == "A-subject-1"
    elif read == "edge":
        payload = server.edge_messages_payload("SharedAgent", "PeerAgent")
        assert payload["ok"], payload
        assert {entry["id"] for entry in payload["messages"]} == {1001, 1002}
    elif read == "since":
        payload = server.messages_since_payload(int(projects.now.timestamp()) - 300)
        assert payload["ok"], payload
        assert {entry["id"] for entry in payload["messages"]} == {1001, 1002}
        assert all(entry["excerpt"] == "A-body" for entry in payload["messages"])
    else:
        assert server._agent_id_for_name("SharedAgent") == 1
        assert server._agent_window("SharedAgent")[0] == int(projects.inception.timestamp())


def test_history_cache_never_reuses_another_project_or_unconfigured_selection(projects, monkeypatch):
    first = server.agent_history_payload("SharedAgent")
    assert first["ok"], first
    assert any(event.get("subject", "").startswith("A-subject") for event in first["events"])
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.other))
    second = server.agent_history_payload("SharedAgent")
    assert second["ok"], second
    assert any(event.get("subject", "").startswith("B-subject") for event in second["events"])
    assert not any(event.get("subject", "").startswith("A-subject") for event in second["events"])
    monkeypatch.setattr(server, "PROJECT_KEY", "")
    assert not server.agent_history_payload("SharedAgent")["ok"]
    assert server._agent_id_for_name("SharedAgent") is None
    assert server._agent_window("SharedAgent") == (0, 0)
    assert server.messages_since_payload(0)["messages"] == []


def test_codex_app_runtime_and_cache_require_project_and_repository(projects, monkeypatch):
    records = [
        {"agent_name": "SharedAgent", "project_key": str(projects.main),
         "cwd": str(projects.linked), "last_seen_at": "2026-09-15T00:00:00Z", "model": "A"},
        {"agent_name": "SharedAgent", "project_key": str(projects.other),
         "cwd": str(projects.other), "last_seen_at": "2026-09-15T00:00:20Z", "model": "B"},
        {"agent_name": "Contradictory", "project_key": str(projects.main),
         "cwd": str(projects.other), "last_seen_at": "2026-09-15T00:00:30Z", "model": "bad"},
    ]
    provider = SimpleNamespace(list_runtimes=lambda: [SimpleNamespace(metadata=entry) for entry in records])
    monkeypatch.setattr(server, "_CODEX_APP_PROVIDER", provider)
    first = server._codex_app_runtimes()
    assert first["SharedAgent"]["model"] == "A"
    assert "Contradictory" not in first
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.other))
    assert server._codex_app_runtimes()["SharedAgent"]["model"] == "B"
    monkeypatch.setattr(server, "PROJECT_KEY", "")
    assert server._codex_app_runtimes() == {}


def _index(projects, agent_id: int, project: Path, *, schema: int = 3) -> Path:
    index_dir = Path(server.SESSION_INDEX_DIR)
    index_dir.mkdir(exist_ok=True)
    transcript = index_dir / f"transcript-{agent_id}.jsonl"
    transcript.write_text(json.dumps({"cwd": str(project), "agent_name": "SharedAgent"}) + "\n")
    (index_dir / f"{agent_id}.json").write_text(json.dumps({
        "schema_version": schema, "binding_kind": "self", "agent_name": "SharedAgent",
        "registered_by": "SharedAgent", "project_key": str(project),
        "transcript_path": str(transcript),
    }))
    return transcript


@pytest.mark.parametrize("resolver", ["_transcript_path", "_codex_transcript_path"])
@pytest.mark.parametrize("schema", [2, 3])
def test_exact_transcript_index_and_cache_stay_project_scoped(projects, monkeypatch, resolver, schema):
    first = _index(projects, 1, projects.main, schema=schema)
    second = _index(projects, 3, projects.other, schema=schema)
    resolve = getattr(server, resolver)
    assert resolve("SharedAgent") == str(first)
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.other))
    assert resolve("SharedAgent") == str(second)
    monkeypatch.setattr(server, "PROJECT_KEY", "")
    assert resolve("SharedAgent") is None


def test_exact_index_rejects_foreign_transcript_even_with_matching_entry_project(projects):
    foreign = _index(projects, 1, projects.main)
    foreign.write_text(json.dumps({"cwd": str(projects.other)}) + "\n")
    assert server._indexed_transcript("SharedAgent") is None


def test_claude_cwd_folder_is_not_authority_for_a_foreign_transcript(projects, tmp_path, monkeypatch):
    import re
    folder = tmp_path / "claude" / re.sub(r"[^A-Za-z0-9]", "-", str(projects.linked))
    folder.mkdir(parents=True)
    own = folder / "own.jsonl"
    own.write_text(json.dumps({"cwd": str(projects.main), "agent_name": "SharedAgent"}) + "\n")
    foreign = folder / "foreign.jsonl"
    foreign.write_text(json.dumps({"cwd": str(projects.other), "agent_name": "SharedAgent",
                                   "text": '"SharedAgent" ' * 100}) + "\n")
    monkeypatch.setattr(server, "CLAUDE_PROJECTS", str(folder.parent))
    run = server.subprocess.run

    def fake_tmux(args, **kwargs):
        if args[:2] == ["tmux", "display-message"]:
            return SimpleNamespace(returncode=0, stdout=str(projects.linked), stderr="")
        return run(args, **kwargs)

    monkeypatch.setattr(server.subprocess, "run", fake_tmux)
    assert server._all_transcripts() == [str(own)]
    assert server._transcript_path("SharedAgent") == str(own)


def test_codex_timestamp_fallback_cannot_choose_a_closer_foreign_rollout(projects, tmp_path, monkeypatch):
    sessions = tmp_path / "rollouts"
    sessions.mkdir()
    for label, cwd, offset in (("own", projects.linked, 20), ("foreign", projects.other, 0)):
        (sessions / f"rollout-{label}.jsonl").write_text(json.dumps({
            "type": "session_meta", "payload": {
                "cwd": str(cwd), "timestamp": (projects.inception + timedelta(seconds=offset)).isoformat(),
            },
        }) + "\n")
    monkeypatch.setattr(server, "_CODEX_SESSIONS_DIR", str(sessions))
    assert server._codex_transcript_path("SharedAgent") == str(sessions / "rollout-own.jsonl")


def test_notification_counts_and_cache_require_recorded_full_project_owner(projects, tmp_path, monkeypatch):
    now = int(projects.now.timestamp())
    state = tmp_path / "notify-state.json"
    state.write_text(json.dumps({
        "a": {"project_key": str(projects.main), "last_result": "success",
              "last_attempt_epoch": now - 30, "last_success_epoch": now - 30},
        "b": {"project_key": str(projects.other), "last_result": "inject_failed",
              "last_attempt_epoch": now - 5, "last_success_epoch": now - 5},
        "legacy": {"last_result": "must-not-leak", "last_attempt_epoch": now},
    }))
    signals = tmp_path / "signals"
    for project in (projects.main, projects.other):
        directory = signals / server._slugify_project_key(str(project)) / "agents" / "SharedAgent"
        directory.mkdir(parents=True)
        (directory / "owned.signal").write_text(json.dumps({"project_key": str(project)}))
        (directory / "legacy.signal").write_text("{}")
        (directory / "foreign.signal").write_text(json.dumps({"project_key": "foreign-project"}))
    monkeypatch.setattr(server, "NOTIFY_STATE_FILE", str(state))
    monkeypatch.setattr(server, "SIGNAL_PROJECTS_DIR", str(signals))
    monkeypatch.setattr(server, "_launchctl_job_running", lambda _label: False)
    monkeypatch.setattr(server, "_systemd_user_unit_running", lambda _label: False)
    monkeypatch.setattr(server, "_pidfile_process_running", lambda *_args: (True, 1234))
    first = server.mail_watcher_health()
    assert first["signal_count"] == 1
    assert first["recent_results"] == {"success": 1}
    assert first["last_success_ts"] == now - 30
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.other))
    second = server.mail_watcher_health()
    assert second["signal_count"] == 1
    assert second["recent_results"] == {"inject_failed": 1}
    assert second["last_success_ts"] == now - 5
    monkeypatch.setattr(server, "PROJECT_KEY", "")
    unknown = server.mail_watcher_health()
    assert unknown["signal_count"] == 0
    assert unknown["recent_results"] == {}
    assert unknown["last_success_ts"] is None


@pytest.mark.parametrize("read", ["capture", "ttyd"])
def test_terminal_reads_reject_foreign_live_name_before_capture_or_process_start(monkeypatch, read):
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "project-a")
    monkeypatch.setattr(server, "tmux_state", lambda: {"SharedAgent": {"cwd": ""}})
    monkeypatch.setattr(server, "_tmux_session_env", lambda *_args: "project-b")

    def forbidden(*_args, **_kwargs):
        pytest.fail("foreign terminal must not be captured or exposed")

    monkeypatch.setattr(server, "_has_session", lambda _name: True)
    monkeypatch.setattr(server.subprocess, "run", forbidden)
    monkeypatch.setattr(server.subprocess, "Popen", forbidden)
    result = server.term_capture("SharedAgent", 100) if read == "capture" else server.ttyd_ensure("SharedAgent")
    assert not result["ok"]
    assert "project" in result["error"]


def test_observed_activity_uses_project_and_name(monkeypatch):
    monkeypatch.setattr(server, "_observed", {})
    assert server._observe_activity(("a", "SharedAgent"), ("working",), 1.0) is None
    assert server._observe_activity(("a", "SharedAgent"), ("waiting",), 2.0) == 2.0
    assert server._observe_activity(("b", "SharedAgent"), ("waiting",), 3.0) is None


def test_stale_ambient_keys_and_markers_cannot_redirect_read_context(projects, monkeypatch):
    for key in ("AGENTSTACK_PROJECT_KEY", "PROJECT_KEY", "AGENTSTACK_PROJECT_REPOSITORY",
                "AGENTSTACK_PROJECT_WORK_DIR"):
        monkeypatch.setenv(key, str(projects.other))
    monkeypatch.setenv("AGENTSTACK_PROJECT_CONTEXT", "1")
    assert server._project_key() == str(projects.main)
    assert server._cwd_matches_dashboard_project(str(projects.linked))
    assert not server._cwd_matches_dashboard_project(str(projects.other))


def test_explicit_namespace_requires_complete_current_context_not_a_marker(projects, monkeypatch):
    key = str(projects.linked)
    data = server._invocation_context(str(projects.linked), key)
    assert data is not None
    monkeypatch.setenv("AGENTSTACK_PROJECT_KEY", key)
    monkeypatch.setenv("AGENTSTACK_PROJECT_WORK_DIR", data["work_dir"])
    monkeypatch.setenv("AGENTSTACK_PROJECT_REPOSITORY", data["repository_key"])
    monkeypatch.setenv("AGENTSTACK_PROJECT_CONTEXT", "1")
    # A legacy marker alone cannot make a worktree literal a new namespace.
    assert server._project_key() == str(projects.main)
    monkeypatch.setenv("AGENTSTACK_PROJECT_CONTEXT_JSON", json.dumps(data))
    assert server._project_key() == key
    assert server._cwd_matches_dashboard_project(str(projects.main))
    assert not server._cwd_matches_dashboard_project(str(projects.other))
    tampered = {**data, "protected_roots": [str(projects.other)]}
    monkeypatch.setenv("AGENTSTACK_PROJECT_CONTEXT_JSON", json.dumps(tampered))
    assert server._project_key() == str(projects.main)


def test_missing_resolver_fails_closed_for_a_configured_worktree(projects, monkeypatch):
    monkeypatch.setattr(server, "_project_context_helper", lambda: "")
    assert server._project_key() == ""
    assert server.agentmail_state() == ({}, {})
    assert not server._cwd_matches_dashboard_project(str(projects.linked))



def test_name_picker_queries_and_cache_never_use_foreign_registration(projects, monkeypatch):
    with sqlite3.connect(projects.db) as con:
        con.execute("UPDATE agents SET name='SunnyCurie' WHERE id=1")
        con.execute("UPDATE agents SET name='ZestyCurie' WHERE id=3")
    monkeypatch.setattr(server, "_SPAWN_STATUS_CACHE", {"ts": 0.0, "key": None, "data": {}})
    assert server._spawn_name_status("Sunny-Curie") == "occupied"
    assert server._spawn_name_status("Zesty-Curie") == "available"
    assert server._spawn_scientist_statuses(["Sunny"], ["Curie"]) == {"Curie": "occupied"}
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.other))
    assert server._spawn_name_status("Sunny-Curie") == "available"
    assert server._spawn_name_status("Zesty-Curie") == "occupied"
    assert server._spawn_scientist_statuses(["Sunny"], ["Curie"]) == {"Curie": "available"}
    monkeypatch.setattr(server, "PROJECT_KEY", "")
    assert server._spawn_name_status("Sunny-Curie") == "unknown"
    assert server._spawn_scientist_statuses(["Sunny"], ["Curie"]) == {"Curie": "unknown"}


def test_async_spawn_results_use_captured_project_not_completion_environment(projects, monkeypatch):
    monkeypatch.setattr(server, "_SPAWN_LAUNCHES", {})
    server._spawn_launch_record("SharedAgent", {"ok": True, "pending": True})
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.other))
    assert not server.spawn_launch_status("SharedAgent")["ok"]
    assert server.spawn_launch_statuses()["launches"] == {}
    server._spawn_launch_record("SharedAgent", {"ok": False, "error": "B failure"})
    server._spawn_launch_record("SharedAgent", {"ok": True, "detail": "A ready"}, str(projects.main))
    assert server.spawn_launch_status("SharedAgent")["error"] == "B failure"
    monkeypatch.setattr(server, "PROJECT_KEY", str(projects.main))
    assert server.spawn_launch_status("SharedAgent")["detail"] == "A ready"
    assert set(server.spawn_launch_statuses()["launches"]) == {"SharedAgent"}
    monkeypatch.setattr(server, "PROJECT_KEY", "")
    assert not server.spawn_launch_status("SharedAgent")["ok"]
    assert server.spawn_launch_statuses()["launches"] == {}
    before = dict(server._SPAWN_LAUNCHES)
    server._spawn_launch_record("UnknownAgent", {"ok": True})
    assert server._SPAWN_LAUNCHES == before


@pytest.mark.parametrize("malformed", [None, [], "not-an-object"])
def test_malformed_index_is_not_transcript_ownership(projects, malformed):
    _index(projects, 1, projects.main)
    (Path(server.SESSION_INDEX_DIR) / "1.json").write_text(json.dumps(malformed))
    assert server._indexed_transcript("SharedAgent") is None


def test_transcript_cwd_skips_non_object_json_rows(projects, tmp_path):
    path = tmp_path / "sparse.jsonl"
    path.write_text('null\n[]\n"noise"\n' + json.dumps({"cwd": str(projects.linked)}) + "\n")
    assert server._transcript_matches_dashboard_project(str(path))
