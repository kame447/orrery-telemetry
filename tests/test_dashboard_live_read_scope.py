"""Project-scoped live Dashboard read regression tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from dashboard import server


def _mail_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    try:
        con.executescript(
            """
            CREATE TABLE projects (id INTEGER PRIMARY KEY, human_key TEXT);
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                name TEXT,
                model TEXT,
                program TEXT,
                task_description TEXT,
                last_active_ts TEXT,
                retired_at TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                sender_id INTEGER,
                subject TEXT,
                created_ts TEXT,
                importance TEXT
            );
            CREATE TABLE message_recipients (
                message_id INTEGER,
                agent_id INTEGER
            );
            INSERT INTO projects VALUES (1, '/project/A');
            INSERT INTO projects VALUES (2, '/project/B');

            INSERT INTO agents VALUES
              (1, 1, 'SharedAgent', 'model-a', 'claude-code', 'task-a',
               '2026-09-15 01:00:00', NULL),
              (2, 1, 'SenderA', 'sender-a', 'claude-code', '',
               '2026-09-15 01:00:00', NULL),
              (3, 2, 'SharedAgent', 'model-b', 'codex-cli', 'task-b',
               '2026-09-15 02:00:00', NULL),
              (4, 2, 'SenderB', 'sender-b', 'codex-cli', '',
               '2026-09-15 02:00:00', NULL);

            INSERT INTO messages VALUES
              (101, 1, 2, 'instruction-a', '2026-09-15 01:10:00', 'high'),
              (201, 2, 4, 'instruction-b', '2026-09-15 02:10:00', 'urgent');
            INSERT INTO message_recipients VALUES (101, 1);
            INSERT INTO message_recipients VALUES (201, 3);
            """
        )
        con.commit()
    finally:
        con.close()
    return path


def test_agentmail_state_never_prefers_newer_same_name_from_other_project(
    tmp_path: Path, monkeypatch
):
    database = _mail_db(tmp_path / 'mail.sqlite3')
    monkeypatch.setattr(server, 'DB_PATH', str(database))
    server._RETIRED_AT_CACHE.clear()
    monkeypatch.setattr(
        server, '_canonical_dashboard_project_key', lambda: '/project/A'
    )

    agents, instructions = server.agentmail_state()

    assert agents['SharedAgent']['model_raw'] == 'model-a'
    assert agents['SharedAgent']['program'] == 'claude-code'
    assert agents['SharedAgent']['task'] == 'task-a'
    assert instructions['SharedAgent']['subject'] == 'instruction-a'
    assert instructions['SharedAgent']['sender'] == 'SenderA'
    assert all(row.get('task') != 'task-b' for row in agents.values())


def test_agentmail_state_fails_closed_without_canonical_project(tmp_path: Path, monkeypatch):
    database = _mail_db(tmp_path / 'mail.sqlite3')
    monkeypatch.setattr(server, 'DB_PATH', str(database))
    server._RETIRED_AT_CACHE.clear()
    monkeypatch.setattr(server, '_canonical_dashboard_project_key', lambda: '')
    assert server.agentmail_state() == ({}, {})


def test_tmux_state_captures_live_pane_cwd_and_keeps_legacy_row_compat(monkeypatch):
    sep = server.SEP
    calls = []

    def fake_tmux(args):
        calls.append(args)
        if args[0] == 'list-sessions':
            return sep.join(['BlueLake', '100', '110', '$1']) + '\n'
        if args[0] == 'list-panes':
            return sep.join([
                'BlueLake', '11', 'zsh', '1234', '/workspace/project-a', 'Claude'
            ]) + '\n'
        if args[0] == 'list-clients':
            return ''
        return ''

    monkeypatch.setattr(server, '_tmux', fake_tmux)
    monkeypatch.setattr(server, '_prune_runtime_cache', lambda _sessions: None)
    state = server.tmux_state()
    assert state['BlueLake']['cwd'] == '/workspace/project-a'
    assert any('#{pane_current_path}' in field for field in calls[1])

    def legacy_tmux(args):
        if args[0] == 'list-sessions':
            return sep.join(['BlueLake', '100', '110', '$1']) + '\n'
        if args[0] == 'list-panes':
            return sep.join(['BlueLake', '11', 'zsh', '1234', 'Claude']) + '\n'
        return ''

    monkeypatch.setattr(server, '_tmux', legacy_tmux)
    assert server.tmux_state()['BlueLake']['cwd'] == ''


def test_raw_graph_cache_is_scoped_by_project_and_live_lineage(monkeypatch):
    calls = []
    current = {'project': '/project/A'}

    class FakeGraph:
        @staticmethod
        def build_graph(*, project_human_key, live_parents):
            calls.append((project_human_key, tuple(live_parents)))
            return {'nodes': [], 'edges': [], 'spawn': []}

    monkeypatch.setitem(__import__('sys').modules, 'graph_data', FakeGraph)
    monkeypatch.setattr(
        server, '_canonical_dashboard_project_key', lambda: current['project']
    )
    server._GRAPH_CACHE.update(
        ts=0, project_key=None, live_parents=None, data=None
    )
    monkeypatch.setattr(server.time, 'time', lambda: 100.0)

    lineage = (('Child', 'Parent'),)
    server._raw_graph(lineage)
    server._raw_graph(lineage)
    assert calls == [('/project/A', lineage)]

    current['project'] = '/project/B'
    server._raw_graph(lineage)
    assert calls[-1] == ('/project/B', lineage)
    assert len(calls) == 2

    server._raw_graph((('OtherChild', 'Parent'),))
    assert len(calls) == 3


def test_build_agents_excludes_foreign_live_session(monkeypatch):
    base = {
        'created': 100,
        'session_id': '$1',
        'activity': 200,
        'attached': False,
        'client_tty': None,
        'cmd': 'zsh',
        'pane_pid': 0,
        'cwd': '',
        'title': 'Claude',
    }
    monkeypatch.setattr(server, 'tmux_state', lambda: {
        'Owned': {'name': 'Owned', **base},
        'Foreign': {'name': 'Foreign', **base},
    })
    monkeypatch.setattr(server, 'agentmail_state', lambda: ({
        'Owned': {
            'model': 'Opus 4.7', 'model_raw': 'opus-4.7', 'program': 'claude-code',
            'task': 'owned', 'last_active': 150,
        },
        'Foreign': {
            'model': 'GPT 5.6', 'model_raw': 'gpt-5.6', 'program': 'codex-cli',
            'task': 'foreign', 'last_active': 160,
        },
    }, {}))
    monkeypatch.setattr(server, '_session_matches_dashboard_project',
                        lambda name, _session, _cache=None: name == 'Owned')
    monkeypatch.setattr(server, '_canonical_dashboard_project_key', lambda: '')
    monkeypatch.setattr(server, '_process_tree_snapshot', lambda: None)
    monkeypatch.setattr(server, '_codex_app_runtimes', lambda: {})
    monkeypatch.setattr(server, '_deliverables_index', lambda: {})
    monkeypatch.setattr(server, '_retired_names', lambda _project: set())
    monkeypatch.setattr(server, '_name_substitutions', lambda: {})
    monkeypatch.setattr(server, '_agent_runtime', lambda *_args: {})

    rows = server.build_agents()
    assert [row['name'] for row in rows] == ['Owned']


def test_graph_payload_passes_only_gated_sessions_to_lineage(monkeypatch):
    sessions = {
        'Owned': {'name': 'Owned', 'cwd': '/a'},
        'Foreign': {'name': 'Foreign', 'cwd': '/b'},
    }
    seen = {}
    monkeypatch.setattr(server, 'tmux_state', lambda: sessions)
    monkeypatch.setattr(server, '_session_matches_dashboard_project',
                        lambda name, _session, _cache=None: name == 'Owned')

    def scoped(filtered):
        seen['names'] = sorted(filtered)
        return ()

    monkeypatch.setattr(server, '_scoped_live_parents', scoped)
    monkeypatch.setattr(server, '_raw_graph', lambda _parents=None: {
        'nodes': [], 'edges': [], 'spawn': [],
        'timestamp_diagnostics': {'invalid_count': 0, 'fields': {}},
        'degraded': False,
    })
    monkeypatch.setattr(server, '_annotations', lambda: {})
    payload = server.graph_payload(4, True)
    assert seen['names'] == ['Owned']
    assert payload['nodes'] == []
