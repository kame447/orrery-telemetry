"""Cross-project archive is visible, but current-project commands remain scoped."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path

import pytest

from dashboard import server


@pytest.fixture
def projects(tmp_path, monkeypatch):
    current = str(tmp_path / "current")
    foreign = str(tmp_path / "foreign")
    db = tmp_path / "mail.sqlite3"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db) as con:
        con.executescript("""
            CREATE TABLE projects (id INTEGER PRIMARY KEY, human_key TEXT);
            CREATE TABLE agents (id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT,
                model TEXT, program TEXT, task_description TEXT, last_active_ts TEXT,
                inception_ts TEXT, retired_at TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, project_id INTEGER,
                sender_id INTEGER, subject TEXT, body_md TEXT, created_ts TEXT, importance TEXT);
            CREATE TABLE message_recipients (message_id INTEGER, agent_id INTEGER, kind TEXT);
        """)
        con.executemany("INSERT INTO projects VALUES (?, ?)", [(1, current), (2, foreign)])
        con.executemany(
            "INSERT INTO agents VALUES (?, ?, ?, 'sonnet', 'claude-code', '', ?, ?, ?)",
            [(1, 1, 'SharedAgent', now, now, now),
             (2, 2, 'SharedAgent', now, now, now),
             (3, 2, 'ForeignAgent', now, now, now)],
        )
    projects_dir = tmp_path / 'claude' / 'projects'
    projects_dir.mkdir(parents=True)
    index = tmp_path / 'session-index'
    index.mkdir()
    sid = 'ac4f86bc-1abc-4abc-8abc-123456789abc'
    transcripts = projects_dir / 'foreign-project'
    transcripts.mkdir()
    transcript = transcripts / f'{sid}.jsonl'
    transcript.write_text(json.dumps({"type": "user", "sessionId": sid,
        "message": {"role": "user", "content": "foreign record"}}) + '\n')
    (index / '3.json').write_text(json.dumps({
        "schema_version": 2, "binding_kind": "self", "agent_id": 3,
        "agent_name": "ForeignAgent", "registered_by": "ForeignAgent",
        "project_key": foreign, "session_id": sid,
        "transcript_path": str(transcript), "cwd": foreign,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }))
    shared_sid = 'ac4f86bc-1abc-4abc-8abc-123456789def'
    shared_transcript = transcripts / f'{shared_sid}.jsonl'
    shared_transcript.write_text(json.dumps({"type": "user", "sessionId": shared_sid,
        "message": {"role": "user", "content": "foreign shared record"}}) + '\n')
    (index / '2.json').write_text(json.dumps({
        "schema_version": 2, "binding_kind": "self", "agent_id": 2,
        "agent_name": "SharedAgent", "registered_by": "SharedAgent",
        "project_key": foreign, "session_id": shared_sid,
        "transcript_path": str(shared_transcript), "cwd": foreign,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }))
    monkeypatch.setattr(server, 'DB_PATH', str(db))
    monkeypatch.setattr(server, '_project_key', lambda: current)
    monkeypatch.setattr(server, 'SESSION_INDEX_DIR', str(index))
    monkeypatch.setattr(server, 'CLAUDE_PROJECTS', str(projects_dir))
    for name, val in (("tmux_state", {}), ("_codex_app_runtimes", {}),
                      ("_deliverables_index", {}), ("_persistent_profiles", {}),
                      ("_name_substitutions", {})):
        monkeypatch.setattr(server, name, lambda val=val: val)
    monkeypatch.setattr(server, '_resume_capability_for_row', lambda *a, **kw: 'ready')
    monkeypatch.setattr(server, '_claude_row_mail_fields', lambda *a, **kw: {})
    server._RETIRED_AT_CACHE.clear()
    yield current, foreign, transcript
    server._RETIRED_AT_CACHE.clear()


def test_cross_project_history_rows_have_a_separate_identity(projects):
    current, foreign, _ = projects
    rows = server.build_agents(None)
    assert len(rows) == 3
    assert {(r['project_key'], r['name']) for r in rows} == {
        (current, 'SharedAgent'), (foreign, 'SharedAgent'), (foreign, 'ForeignAgent')}
    same_name = [r for r in rows if r['name'] == 'SharedAgent']
    assert len(same_name) == 2
    assert {r['archive_only'] for r in same_name} == {True, False}
    other = next(r for r in rows if r['name'] == 'ForeignAgent')
    assert other['archive_only'] is True
    assert other['resume_capability'] == 'unavailable'
    assert other['history_binding'] == 'bound'
    assert other['transcript_path']
    assert server.lookup_agent('ForeignAgent') is None
    assert server.lookup_agent('SharedAgent')['program'] == 'claude-code'
    # The same agent name in a different project selects that project's receipt.
    shared = server.history_payload('SharedAgent', 20, project_key=foreign)
    assert shared['ok']
    assert any('foreign shared record' in event['text'] for event in shared['events'])


def test_foreign_history_uses_verified_receipt_and_never_scans(projects, monkeypatch):
    _, foreign, transcript = projects
    monkeypatch.setattr(server, '_transcript_path', lambda *_: (_ for _ in ()).throw(
        AssertionError('cross-project heuristic transcript scan')))
    result = server.history_payload('ForeignAgent', 20, project_key=foreign)
    assert result['ok'] and result['file'] == transcript.name
    assert any('foreign record' in event['text'] for event in result['events'])
    # A missing foreign record never falls through to the current project's name.
    assert not server.history_payload('NotAnAgent', 20, project_key=foreign)['ok']
    transcript.unlink()
    assert not server.history_payload('ForeignAgent', 20, project_key=foreign)['ok']


def test_deck_routes_archive_by_project_and_avoids_cross_project_controls():
    html = (Path(__file__).resolve().parents[1] / 'dashboard' / 'index.html').read_text()
    assert 'data-project="${esc(a.project_key||\'\')}"' in html
    assert 'openPanel(this.dataset.name,this.dataset.project)' in html
    assert "const unavailable=!!a.archive_only" in html
    assert 'if(panelProject&&panelProject!==activeProjectKey)' in html
    assert "'&project_key='+encodeURIComponent(requestedProject)" in html


def test_lan_bound_dashboard_keeps_the_legacy_project_scope(projects, monkeypatch):
    current, foreign, _ = projects
    monkeypatch.setattr(server, 'BIND_HOST', '0.0.0.0')
    rows = server.build_agents(None)
    assert {(r['project_key'], r['name']) for r in rows} == {(current, 'SharedAgent')}
    refused = server.history_payload('ForeignAgent', 20, project_key=foreign)
    assert refused == {"ok": False, "error": "Cross-project history is local-only"}


def test_foreign_dsh_archive_does_not_bind_a_same_named_current_agent(projects, monkeypatch):
    current, foreign, _ = projects
    with sqlite3.connect(server.DB_PATH) as con:
        con.execute("UPDATE agents SET program = 'dsh' WHERE id = 2")
    monkeypatch.setattr(server, '_dsh_history_binding', lambda *a, **kw: (
        (_ for _ in ()).throw(AssertionError('foreign dsh read current project'))))
    rows = server.build_agents(None)
    other = next(r for r in rows if r['name'] == 'SharedAgent' and r['project_key'] == foreign)
    assert other['history_binding'] == 'unconfirmed'
    assert other['history_binding_reason_code'] == 'unsupported_cross_project_provider'
    assert other['transcript_path'] is None
    assert server.lookup_agent('SharedAgent') is not None
