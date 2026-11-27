"""Parent/child lineage survives a spawn that sends no delegation mail."""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from dashboard import graph_data


PROJECT = "/lineage-fixture"
BASE = 1_786_000_000


def _make_db(path: pathlib.Path) -> pathlib.Path:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(f"""
            CREATE TABLE projects (id INTEGER PRIMARY KEY, human_key TEXT);
            CREATE TABLE agents (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                name TEXT,
                model TEXT,
                program TEXT,
                task_description TEXT,
                last_active_ts INTEGER,
                inception_ts INTEGER,
                retired_at TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                sender_id INTEGER,
                created_ts INTEGER,
                importance TEXT
            );
            CREATE TABLE message_recipients (
                message_id INTEGER,
                agent_id INTEGER,
                kind TEXT
            );
            INSERT INTO projects VALUES (1, '{PROJECT}');
        """)
        # Quiet child: registered, never sent or received anything. This is what
        # an --embed-task spawn looks like until it reports back.
        for agent_id, name, inception in (
            (1, "Parent", BASE),
            (2, "QuietChild", BASE + 60),
        ):
            connection.execute(
                "INSERT INTO agents VALUES (?, 1, ?, 'model', 'codex', 'task',"
                " ?, ?, NULL)",
                (agent_id, name, (inception + 600) * 1_000_000,
                 inception * 1_000_000),
            )
        connection.commit()
    finally:
        connection.close()
    return path


def _fake_tmux(directory: pathlib.Path, show_environment_case: str, *, session: str = "child") -> pathlib.Path:
    fake = directory / "tmux"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  list-sessions) printf "{session}\\n" ;;\n'
        f"{show_environment_case}"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _build(monkeypatch, database: pathlib.Path, live: dict[str, str]) -> dict:
    monkeypatch.setattr(graph_data, "DB_PATH", str(database))
    monkeypatch.setattr(graph_data, "PROJECT_HUMAN_KEY", PROJECT)
    monkeypatch.setattr(graph_data, "PROJECT_ID", 1)
    monkeypatch.setattr(graph_data, "_live_parents", lambda project_key: dict(live))
    return graph_data.build_graph()


def test_spawn_edge_without_any_message(tmp_path, monkeypatch):
    database = _make_db(tmp_path / "mail.sqlite3")

    graph = _build(monkeypatch, database, {"QuietChild": "Parent"})

    assert graph["spawn"] == [
        {"source": "Parent", "target": "QuietChild", "type": "spawn"}
    ]


def test_message_derived_lineage_still_applies_without_a_live_session(
    tmp_path, monkeypatch
):
    database = _make_db(tmp_path / "mail.sqlite3")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO messages VALUES (1, 1, 1, ?, 'high')",
            ((BASE + 120) * 1_000_000,),
        )
        connection.execute("INSERT INTO message_recipients VALUES (1, 2, 'to')")
        connection.commit()
    finally:
        connection.close()

    graph = _build(monkeypatch, database, {})

    assert graph["spawn"] == [
        {"source": "Parent", "target": "QuietChild", "type": "spawn"}
    ]


def test_unknown_and_self_referential_sessions_are_ignored(tmp_path, monkeypatch):
    database = _make_db(tmp_path / "mail.sqlite3")

    graph = _build(
        monkeypatch,
        database,
        {
            "QuietChild": "SomeoneNotRegistered",  # parent outside the project
            "Stranger": "Parent",                  # session that is not an agent
            "Parent": "Parent",                    # a session cannot spawn itself
        },
    )

    assert graph["spawn"] == []


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ("PARENT_AGENT=Parent\n", {"child": "Parent"}),
        ("-PARENT_AGENT\n", {}),          # tmux marks a removed variable this way
        ("PARENT_AGENT=\n", {}),
        ("", {}),
    ],
)
def test_live_parents_parses_tmux_environment(tmp_path, monkeypatch, stdout, expected):
    env_output = tmp_path / "env-output"
    env_output.write_text(stdout + f"AGENTSTACK_PROJECT_KEY={PROJECT}\n", encoding="utf-8")
    _fake_tmux(
        tmp_path,
        f'  show-environment) cat "{env_output}" ;;\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(
        graph_data, "_LIVE_PARENT_CACHE", {"ts": 0.0, "value": None}
    )

    assert graph_data._live_parents(PROJECT) == expected


def test_live_parents_does_not_cache_a_missing_tmux_server(tmp_path, monkeypatch):
    """A failed listing must not be remembered as "no lineage exists".

    A tmux server that is briefly unreachable would otherwise pin an empty
    lineage for the whole TTL, which looks exactly like the bug being fixed.
    """
    marker = tmp_path / "first-call-done"
    fake = tmp_path / "tmux"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  list-sessions)\n"
        f'    if [ -e "{marker}" ]; then printf "child\\n"\n'
        f'    else : > "{marker}"; exit 1; fi ;;\n'
        '  show-environment) printf "PARENT_AGENT=Parent\\nAGENTSTACK_PROJECT_KEY=/lineage-fixture\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(
        graph_data, "_LIVE_PARENT_CACHE", {"ts": 0.0, "value": None}
    )

    assert graph_data._live_parents(PROJECT) == {}
    assert graph_data._live_parents(PROJECT) == {"child": "Parent"}


@pytest.mark.parametrize('raw', ['qwen3.8-flash-next-iq3_xxs', 'glm-5.3-flash', 'deepseek-v4.1-flash'])
def test_graph_nodes_preserve_raw_model_and_local_backend_metadata(monkeypatch, raw):
    from dashboard import server
    node = {'name': 'FixtureLocal', 'program': 'dsh', 'model': raw, 'last_active': BASE}
    monkeypatch.setattr(server, '_raw_graph', lambda: {'nodes': [node], 'edges': [], 'spawn': []})
    monkeypatch.setattr(server, '_graph_keep', lambda *args: ({node['name']}, BASE, {}, {}, None))
    for name in ('_deliverables_index', '_annotations', '_name_substitutions', '_persistent_profiles'):
        monkeypatch.setattr(server, name, lambda: {})
    monkeypatch.setattr(server.harness_runtime, 'metadata', lambda *args: {'provider': 'strata', 'locality': 'local', 'harness': 'dsh'})
    monkeypatch.setattr(server, '_resume_capability_for_row', lambda *args, **kwargs: 'ready')
    monkeypatch.setattr(server, '_claude_row_mail_fields', lambda *args, **kwargs: {})
    row = server.graph_payload(4, True)['nodes'][0]
    assert row['model_raw'] == raw
    assert row['provider'] == 'strata' and row['locality'] == 'local'
    assert row['harness'] == 'dsh'


@pytest.mark.parametrize('raw,label',[('gpt-6.1-sol','GPT-6.1-Sol'),('gpt-7-nova','GPT-7-Nova')])
def test_graph_registered_gpt_models_keep_complete_display_and_raw(monkeypatch,raw,label):
    from dashboard import server
    node={'name':'FixtureGPT','program':'codex','model':raw,'last_active':BASE}
    monkeypatch.setattr(server,'_raw_graph',lambda:{'nodes':[node],'edges':[],'spawn':[]})
    monkeypatch.setattr(server,'_graph_keep',lambda *args:({node['name']},BASE,{},{},None))
    for name in ('_deliverables_index','_annotations','_name_substitutions','_persistent_profiles'):
        monkeypatch.setattr(server,name,lambda:{})
    monkeypatch.setattr(server.harness_runtime,'metadata',lambda *args:{})
    monkeypatch.setattr(server,'_resume_capability_for_row',lambda *args,**kwargs:'ready')
    monkeypatch.setattr(server,'_claude_row_mail_fields',lambda *args,**kwargs:{})
    payload=server.graph_payload(4,True)
    assert payload['nodes'][0]['model']==label
    assert payload['nodes'][0]['model_raw']==raw
    assert payload['nodes'][0]['provider']=='openai'
    assert len(payload['nodes'])==1 and payload['edges']==[]


def test_live_lineage_project_scope_survives_cache_reuse(tmp_path, monkeypatch):
    database = _make_db(tmp_path / "mail.sqlite3")
    foreign = "/foreign-project"
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO projects VALUES (2, ?)", (foreign,))
        connection.execute(
            "INSERT INTO agents SELECT id+2,2,name,model,program,task_description,"
            "last_active_ts,inception_ts,retired_at FROM agents WHERE project_id=1"
        )
    _fake_tmux(
        tmp_path,
        '  show-environment) printf "PARENT_AGENT=Parent\\n'
        'AGENTSTACK_PROJECT_KEY=/foreign-project\\n" ;;\n',
        session="QuietChild",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("AGENTSTACK_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(graph_data, "DB_PATH", str(database))
    monkeypatch.setattr(graph_data, "_LIVE_PARENT_CACHE", {"ts": 0.0, "value": None})
    monkeypatch.setattr(graph_data, "_PARENT_BY_SESSION", {})
    monkeypatch.setattr(graph_data, "PROJECT_HUMAN_KEY", PROJECT)
    assert graph_data.build_graph()["spawn"] == []
    monkeypatch.setattr(graph_data, "PROJECT_HUMAN_KEY", foreign)
    assert graph_data.build_graph()["spawn"] == [
        {"source": "Parent", "target": "QuietChild", "type": "spawn"}
    ]
    monkeypatch.setattr(graph_data, "PROJECT_HUMAN_KEY", PROJECT)
    assert graph_data.build_graph()["spawn"] == []


@pytest.mark.parametrize("scope", ["", "-AGENTSTACK_PROJECT_KEY", "AGENTSTACK_PROJECT_KEY=", "AGENTSTACK_PROJECT_KEY=/foreign"])
def test_live_lineage_requires_explicit_matching_scope(tmp_path, monkeypatch, scope):
    env_output = tmp_path / "env-output"
    env_output.write_text(f"PARENT_AGENT=Parent\n{scope}\n")
    _fake_tmux(tmp_path, f'  show-environment) cat "{env_output}" ;;\n')
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(graph_data, "_LIVE_PARENT_CACHE", {"ts": 0.0, "value": None})
    assert graph_data._live_parents(PROJECT) == {}
