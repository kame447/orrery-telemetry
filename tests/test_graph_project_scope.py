"""Read-only graph project boundaries with no real Mail or tmux access."""
from __future__ import annotations

from contextlib import closing
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROJECT_A = "/graph-fixture/A"
PROJECT_B = "/graph-fixture/B"
BASE_TIME = 1_700_000_000


class GraphProjectScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="orrery-graph-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "mail.sqlite3"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript("""
                CREATE TABLE projects (id INTEGER PRIMARY KEY, human_key TEXT);
                CREATE TABLE agents (
                    id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT,
                    model TEXT, program TEXT, task_description TEXT,
                    retired_at TEXT, last_active_ts INTEGER, inception_ts INTEGER
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, project_id INTEGER, sender_id INTEGER,
                    created_ts INTEGER, importance TEXT
                );
                CREATE TABLE message_recipients (
                    message_id INTEGER, agent_id INTEGER, kind TEXT
                );
            """)
            connection.executemany("INSERT INTO projects VALUES (?, ?)",
                                   [(1, PROJECT_A), (2, PROJECT_B)])
            for project_id, names in ((1, ("Parent", "Child", "OnlyA")),
                                      (2, ("Parent", "Child", "OnlyB"))):
                for offset, name in enumerate(names):
                    agent_id = (project_id - 1) * 3 + offset + 1
                    connection.execute(
                        "INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                        (agent_id, project_id, name, f"model-{project_id}", "codex",
                         f"task-{project_id}", (BASE_TIME + 200) * 1_000_000,
                         (BASE_TIME + offset * 10) * 1_000_000),
                    )
        module_path = ROOT / "dashboard" / "graph_data.py"
        spec = importlib.util.spec_from_file_location("isolated_graph_fixture", module_path)
        assert spec is not None and spec.loader is not None
        self.graph = importlib.util.module_from_spec(spec)
        # Set the DB explicitly before import: graph_data's fallback can inspect
        # a live listener. Any host command during these tests is forbidden.
        self.commands = patch("subprocess.run", side_effect=AssertionError("host command"))
        self.commands.start()
        self.addCleanup(self.commands.stop)
        with patch.dict(os.environ, {
            "AGENTSTACK_MAIL_DB": str(self.database),
            "AGENTSTACK_PROJECT_KEY": PROJECT_A,
        }):
            spec.loader.exec_module(self.graph)
        self.parent_patch = patch.object(self.graph, "_live_parents", return_value={})
        self.parents = self.parent_patch.start()
        self.addCleanup(self.parent_patch.stop)
        self.original_database = self.database.read_bytes()

    def tearDown(self) -> None:
        self.assertEqual(self.database.read_bytes(), self.original_database,
                         "graph read changed its database")

    def add_message(self, message_id: int, project_id: int,
                    sender: int, recipient: int) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, 'high')",
                               (message_id, project_id, sender,
                                (BASE_TIME + 100 + message_id) * 1_000_000))
            connection.execute("INSERT INTO message_recipients VALUES (?, ?, 'to')",
                               (message_id, recipient))
        self.original_database = self.database.read_bytes()

    def assert_empty_graph(self, graph: dict) -> None:
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["spawn"], [])

    def test_unknown_configured_project_does_not_fall_back_to_project_one(self) -> None:
        self.graph.PROJECT_HUMAN_KEY = "/not-registered"
        self.assert_empty_graph(self.graph.build_graph())

    def test_unknown_explicit_project_does_not_fall_back_to_configured_project(self) -> None:
        self.assert_empty_graph(self.graph.build_graph("/not-registered"))
        self.parents.assert_not_called()

    def test_empty_explicit_project_does_not_use_standalone_default(self) -> None:
        self.assert_empty_graph(self.graph.build_graph(""))
        self.parents.assert_not_called()

    def test_explicit_project_overrides_process_default_without_mutation(self) -> None:
        graph = self.graph.build_graph(PROJECT_B)
        self.assertEqual({node["name"] for node in graph["nodes"]},
                         {"Parent", "Child", "OnlyB"})
        self.assertEqual({node["task"] for node in graph["nodes"]}, {"task-2"})
        self.assertEqual(self.graph.PROJECT_HUMAN_KEY, PROJECT_A)
        self.assertEqual(self.graph.PROJECT_ID, 1)
        self.parents.assert_not_called()

    def test_alternating_projects_do_not_reuse_previous_graph(self) -> None:
        for key, only_name in ((PROJECT_A, "OnlyA"), (PROJECT_B, "OnlyB"),
                               (PROJECT_A, "OnlyA")):
            with self.subTest(project=key):
                graph = self.graph.build_graph(key)
                self.assertEqual({node["name"] for node in graph["nodes"]},
                                 {"Parent", "Child", only_name})

    def test_same_names_do_not_merge_messages_or_activity_between_projects(self) -> None:
        self.add_message(1, 1, 1, 2)
        self.add_message(2, 2, 4, 5)
        self.add_message(3, 2, 4, 5)
        first = self.graph.build_graph(PROJECT_A)
        second = self.graph.build_graph(PROJECT_B)
        self.assertEqual([edge["count"] for edge in first["edges"]], [1])
        self.assertEqual([edge["count"] for edge in second["edges"]], [2])
        for graph, expected_activity in ((first, 1), (second, 2)):
            with self.subTest(activity=expected_activity):
                self.assertEqual(graph["spawn"],
                                 [{"source": "Parent", "target": "Child", "type": "spawn"}])
                self.assertEqual({node["act"] for node in graph["nodes"]
                                  if node["name"] in ("Parent", "Child")},
                                 {expected_activity})

    def test_explicit_project_never_uses_global_live_lineage(self) -> None:
        self.parents.return_value = {"Child": "Parent"}
        self.assertEqual(self.graph.build_graph(PROJECT_A)["spawn"], [])
        self.parents.assert_not_called()

    def test_scoped_mapping_supplies_live_lineage_without_global_lookup(self) -> None:
        mapping = {"Child": "Parent", "OnlyB": "Parent", "OnlyA": "OnlyA"}
        graph = self.graph.build_graph(PROJECT_A, mapping)
        self.assertEqual(graph["spawn"],
                         [{"source": "Parent", "target": "Child", "type": "spawn"}])
        self.assertEqual(mapping, {"Child": "Parent", "OnlyB": "Parent", "OnlyA": "OnlyA"})
        self.parents.assert_not_called()

    def test_scoped_iterable_supplies_live_lineage(self) -> None:
        pairs = iter([("Child", "Parent")])
        self.assertEqual(self.graph.build_graph(PROJECT_B, pairs)["spawn"],
                         [{"source": "Parent", "target": "Child", "type": "spawn"}])
        self.parents.assert_not_called()

    def test_scoped_empty_mapping_does_not_fall_back_to_global_lineage(self) -> None:
        self.parents.return_value = {"Child": "Parent"}
        self.assertEqual(self.graph.build_graph(PROJECT_A, {})["spawn"], [])
        self.parents.assert_not_called()

    def test_unconfigured_standalone_default_is_preserved(self) -> None:
        self.graph.PROJECT_HUMAN_KEY = ""
        self.parents.return_value = {"Child": "Parent"}
        graph = self.graph.build_graph()
        self.assertEqual({node["name"] for node in graph["nodes"]},
                         {"Parent", "Child", "OnlyA"})
        self.assertEqual(graph["spawn"],
                         [{"source": "Parent", "target": "Child", "type": "spawn"}])
        self.parents.assert_called_once_with()

    def test_project_key_is_bound_as_sql_parameter(self) -> None:
        self.assert_empty_graph(self.graph.build_graph("' OR 1=1 --"))

    def test_failed_project_lookup_does_not_return_project_one(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            self.assertEqual(self.graph._resolve_project_id(connection, PROJECT_A), -1)

    def test_missing_database_returns_empty_graph_without_host_probe(self) -> None:
        self.graph.DB_PATH = str(self.root / "missing.sqlite3")
        self.assert_empty_graph(self.graph.build_graph(PROJECT_A))
        self.parents.assert_not_called()


if __name__ == "__main__":
    unittest.main()
