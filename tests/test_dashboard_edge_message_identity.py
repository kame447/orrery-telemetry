"""Identity-backed Mail observations use one isolated SQLite read snapshot."""
from __future__ import annotations

import contextlib
import sqlite3

import pytest

from dashboard import server


PROJECT = "/fixture/mcp-selected"
INSTANCE = "12345678-1234-4234-8234-123456789abc"
OTHER_INSTANCE = "abcdefab-cdef-4abc-8def-abcdefabcdef"
IDENTITY_FIELDS = {"mail_instance_id", "sender_agent_id", "recipient_agent_id"}


@pytest.fixture
def mail_database(tmp_path, monkeypatch):
    path = tmp_path / "mail.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE projects (id INTEGER PRIMARY KEY, human_key TEXT);
            CREATE TABLE agents (id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, project_id INTEGER, sender_id INTEGER,
                created_ts TEXT, subject TEXT, body_md TEXT, importance TEXT,
                thread_id TEXT, topic TEXT, ack_required INTEGER
            );
            CREATE TABLE message_recipients (
                message_id INTEGER, agent_id INTEGER, kind TEXT, read_ts TEXT, ack_ts TEXT
            );
            CREATE TABLE mail_instances (id INTEGER PRIMARY KEY, instance_id TEXT);
        """)
        connection.executemany("INSERT INTO projects VALUES (?, ?)", [(1, PROJECT), (2, "/fixture/other")])
        connection.executemany("INSERT INTO agents VALUES (?, ?, ?)", [
            (11, 1, "ChatRelay"), (12, 1, "ExampleWorker"),
            (21, 2, "ChatRelay"), (22, 2, "ExampleWorker"),
        ])
        connection.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, 'normal', ?, NULL, 1)", [
            (100, 1, 11, "2026-11-20 00:00:00", "forward", "original instruction", "request-100"),
            (101, 1, 12, "2026-11-20 00:01:00", "reply", "original response", "request-100"),
            (200, 2, 21, "2026-11-20 00:02:00", "foreign", "foreign instruction", "request-200"),
        ])
        connection.executemany("INSERT INTO message_recipients VALUES (?, ?, 'to', ?, ?)", [
            (100, 12, "2026-11-20 00:00:10", "2026-11-20 00:00:11"),
            (101, 11, None, None), (200, 22, None, None),
        ])
        connection.execute("INSERT INTO mail_instances VALUES (1, ?)", (INSTANCE,))
    monkeypatch.setattr(server, "DB_PATH", str(path))
    monkeypatch.setattr(server, "_project_key", lambda: PROJECT)
    return path


def test_edge_messages_preserve_project_direction_and_receipts(mail_database):
    payload = server.edge_messages_payload("ChatRelay", "ExampleWorker")
    assert payload["ok"] is True and payload["count"] == 2
    messages = {row["id"]: row for row in payload["messages"]}
    assert set(messages) == {100, 101}
    forward, reply = messages[100], messages[101]
    assert {key: forward[key] for key in IDENTITY_FIELDS} == {
        "mail_instance_id": INSTANCE, "sender_agent_id": 11, "recipient_agent_id": 12,
    }
    assert {key: reply[key] for key in IDENTITY_FIELDS} == {
        "mail_instance_id": INSTANCE, "sender_agent_id": 12, "recipient_agent_id": 11,
    }
    assert forward["read_ts"] == "2026-11-20 00:00:10"
    assert forward["ack_ts"] == "2026-11-20 00:00:11"
    assert forward["body"] == "original instruction" and forward["subject"] == "forward"
    assert reply["read_ts"] is None and reply["ack_ts"] is None
    limited = server.edge_messages_payload("ChatRelay", "ExampleWorker", limit=1)
    assert limited["count"] == 1 and limited["messages"][0]["id"] == 101


@pytest.mark.parametrize("identity", ["missing_table", "missing_row", None, "", "not-a-uuid", "a" * 32])
def test_unavailable_identity_keeps_legacy_messages_unverified(mail_database, identity):
    with sqlite3.connect(mail_database) as connection:
        if identity == "missing_table":
            connection.execute("DROP TABLE mail_instances")
        elif identity == "missing_row":
            connection.execute("DELETE FROM mail_instances")
        else:
            connection.execute("UPDATE mail_instances SET instance_id=?", (identity,))
    payload = server.edge_messages_payload("ChatRelay", "ExampleWorker")
    assert payload["ok"] is True and payload["count"] == 2
    assert all(IDENTITY_FIELDS.isdisjoint(row) for row in payload["messages"])
    forward = next(row for row in payload["messages"] if row["id"] == 100)
    assert forward["body"] == "original instruction"
    assert forward["read_ts"] == "2026-11-20 00:00:10" and forward["ack_ts"] == "2026-11-20 00:00:11"


def test_malformed_identity_schema_is_reported(mail_database):
    with sqlite3.connect(mail_database) as connection:
        connection.execute("DROP TABLE mail_instances")
        connection.execute("CREATE TABLE mail_instances (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO mail_instances VALUES (1)")
    payload = server.edge_messages_payload("ChatRelay", "ExampleWorker")
    assert payload["ok"] is False
    assert "OperationalError" in payload["error"]


def test_invalid_agent_id_does_not_claim_verified_identity(mail_database):
    with sqlite3.connect(mail_database) as connection:
        connection.execute("UPDATE agents SET id=0 WHERE id=11")
        connection.execute("UPDATE messages SET sender_id=0 WHERE sender_id=11")
        connection.execute("UPDATE message_recipients SET agent_id=0 WHERE agent_id=11")
    payload = server.edge_messages_payload("ChatRelay", "ExampleWorker")
    assert payload["ok"] is True and payload["count"] == 2
    assert all(IDENTITY_FIELDS.isdisjoint(row) for row in payload["messages"])


def test_identity_and_messages_share_a_read_snapshot(mail_database, monkeypatch):
    changed = []

    def update_before_message_query(statement):
        if changed or not statement.lstrip().upper().startswith("SELECT M.ID"):
            return
        with sqlite3.connect(mail_database) as writer:
            writer.execute("UPDATE mail_instances SET instance_id=? WHERE id=1", (OTHER_INSTANCE,))
            writer.execute("UPDATE messages SET subject='new snapshot' WHERE project_id=1")
        changed.append(True)

    @contextlib.contextmanager
    def traced_database():
        connection = sqlite3.connect(f"file:{mail_database}?mode=ro", uri=True)
        connection.set_trace_callback(update_before_message_query)
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(server, "_db", traced_database)
    payload = server.edge_messages_payload("ChatRelay", "ExampleWorker")
    assert changed == [True]
    assert payload["ok"] is True
    assert {row["mail_instance_id"] for row in payload["messages"]} == {INSTANCE}
    assert {row["subject"] for row in payload["messages"]} == {"forward", "reply"}
    with sqlite3.connect(mail_database) as connection:
        assert connection.execute("SELECT instance_id FROM mail_instances WHERE id=1").fetchone()[0] == OTHER_INSTANCE
        assert connection.execute("SELECT subject FROM messages WHERE id=100").fetchone()[0] == "new snapshot"
