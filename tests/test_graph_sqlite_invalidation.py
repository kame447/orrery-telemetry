"""Exercise both HTTP graph projections against a real SQLite WAL transaction."""
import importlib.util
import json
import pathlib
import runpy
import sqlite3
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

import dashboard.server as server
from tests.test_graph_live_lineage import BASE, PROJECT, _make_db

if server.HERE not in sys.path:
    sys.path.insert(0, server.HERE)
import graph_data


@pytest.mark.parametrize("surface", ["display", "demo"])
def test_graph_refresh_without_new_message(tmp_path, monkeypatch, surface):
    (tmp_path / "mail").mkdir()
    (tmp_path / "payload").mkdir()
    db = _make_db(tmp_path / "mail" / "storage.sqlite3")
    monkeypatch.setattr(graph_data, "DB_PATH", str(db))
    monkeypatch.setattr(graph_data, "PROJECT_HUMAN_KEY", PROJECT)
    monkeypatch.setattr(server, "DB_PATH", str(db))
    # This test exercises the SQL and cache layers independently of live
    # observation. Dedicated tmux tests cover live parent discovery.
    monkeypatch.setattr(graph_data, "_live_parents", lambda *args: {})
    monkeypatch.setattr(graph_data, "_recorded_parents", lambda *args: {})
    monkeypatch.setattr(server, "_GRAPH_CACHE", {"ts": 0, "data": None, "mail": None})
    monkeypatch.setattr(server, "_GRAPH_REUSE", server._TimedReuse(4.0, clock=lambda: 1.0))
    # Freeze raw-cache time, so even a slow runner cannot pass due to TTL.
    class Clock:
        def time(self):
            return 1.0

        def __getattr__(self, name):
            return getattr(real_time, name)

    real_time = server.time
    monkeypatch.setattr(server, "time", Clock())
    writer = sqlite3.connect(db)
    http = worker = None
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO messages VALUES (1,1,1,?,'high')", ((BASE+120)*1000000,))
        writer.execute("INSERT INTO message_recipients VALUES (1,2,'to')")
        writer.commit()
        handler = server.Handler
        if surface == "demo":
            spec = importlib.util.spec_from_file_location(
                "reel_test", pathlib.Path(server.HERE).parent / "scripts/dashboard-demo-reel.py")
            reel = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reel)
            monkeypatch.setitem(sys.modules, "server", server)
            monkeypatch.setattr(sys, "path", list(sys.path))
            handler = runpy.run_path(str(reel._create_reel_server(tmp_path)))["ReadOnlyReelHandler"]
        http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()

        def fetch(projection):
            with urlopen(f"http://127.0.0.1:{http.server_port}/api/graph?all=1&{projection}=1", timeout=10) as response:
                data = json.load(response)
            assert not data.get("degraded"), data
            return data["spawn"]

        edge = {"source": "Parent", "target": "QuietChild", "type": "spawn"}
        assert fetch("spawn_only") == [edge]
        assert fetch("topology_only") == [edge]
        mark = graph_data.mail_mark()
        wal = pathlib.Path(str(db) + "-wal")
        before = (wal.stat().st_size, wal.stat().st_mtime_ns)
        writer.execute("UPDATE messages SET importance='normal' WHERE id=1")
        writer.commit()
        assert before != (wal.stat().st_size, wal.stat().st_mtime_ns)
        assert graph_data.mail_mark() == mark
        assert server._raw_graph()["spawn"] == [edge]
        if surface == "display":
            assert fetch("spawn_only") == [edge]
            assert fetch("topology_only") == [edge]
            server.invalidate_display_reuse()
        assert fetch("spawn_only") == []
        assert fetch("topology_only") == []
    finally:
        if http is not None:
            http.shutdown()
            http.server_close()
            worker.join(5)
        writer.close()
