"""Exact-owner refresh unit tests with real SQLite and archive persistence.

FunctionTool.run exercises the published argument validation and implementation.
These unit tests deliberately do not start the transport or management listener;
the Client-based authentication suite separately covers full server startup.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastmcp import Context
from pydantic import ValidationError
from sqlalchemy import MetaData, delete, select, update

from agentstack_mail import app, config, db
from agentstack_mail.models import Agent, Project
from test_identity_contract import _configure_isolated_runtime, _payload

TOKEN = "existing-owner-refresh-fixture"
STAMP = datetime(2026, 9, 25, tzinfo=timezone.utc)


@pytest.fixture
def isolated_runtime(monkeypatch, tmp_path):
    _configure_isolated_runtime(monkeypatch, tmp_path, mode="passthrough")
    # Await fixture/profile audit commits so background Git lock writes cannot
    # be mistaken for mutation by a rejected request or outlive its event loop.
    monkeypatch.setenv("AGENTSTACK_MAIL_ARCHIVE_COMMIT_ASYNC", "false")
    yield
    asyncio.run(db.dispose_database_for_shutdown())
    app.clear_repo_cache()
    db.reset_database_state()
    config.clear_settings_cache()


async def _prepare(tmp_path, *, token=TOKEN):
    project = await app._ensure_project(str(tmp_path / "project"))
    other = await app._ensure_project(str(tmp_path / "other"))
    async with db.get_session() as session:
        agent = Agent(
            project_id=project.id, name="LegacyResearcher", program="claude-code",
            model="original-model", task_description="original task",
            contact_policy="block_all", attachments_policy="file",
            inception_ts=STAMP, last_active_ts=STAMP, retired_at=STAMP,
            registration_token=token, credential_generation=4,
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
    archive = await app.ensure_archive(config.get_settings(), project.slug)
    profile = app._agent_to_dict(agent)
    profile.update(window_id="existing-window", window_display_name="Existing window")
    async with app._archive_write_lock(archive):
        await app.write_agent_profile(archive, profile)
    return project, other, agent, archive


async def _rows():
    async with db.get_session() as session:
        agents = (await session.execute(select(Agent).order_by(Agent.id))).scalars()
        projects = (await session.execute(select(Project).order_by(Project.id))).scalars()
        return ([row.model_dump(mode="json") for row in agents],
                [row.model_dump(mode="json") for row in projects])


def _files(archive):
    return {str(path.relative_to(archive.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in archive.root.rglob("*") if path.is_file()}


async def _call(project, agent, **overrides):
    server = app.build_mcp_server()
    tool = await server.get_tool("register_agent")
    arguments = dict(
        project_key=project.human_key, name=agent.name, program=agent.program,
        model="new-model", task_description="new task", registration_token=TOKEN,
        existing_agent_id=agent.id, refresh_existing=True,
    )
    arguments.update(overrides)
    return _payload(await tool.run({"ctx": Context(server), **arguments}))


def _forbid_enrollment(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Exact-owner refresh entered enrollment/window mutation")
    for name in ("_get_or_create_agent", "_generate_unique_agent_name",
                 "_create_window_identity", "_touch_window_identity"):
        monkeypatch.setattr(app, name, forbidden)


@pytest.mark.parametrize("mode", ["passthrough", "coerce", "always_auto"])
@pytest.mark.parametrize("attachments", [None, "auto", "inline"])
def test_owned_refresh_changes_only_model_task_and_activity(
    isolated_runtime, monkeypatch, tmp_path, mode, attachments,
):
    monkeypatch.setenv("AGENTSTACK_MAIL_AGENT_NAME_ENFORCEMENT_MODE", mode)
    monkeypatch.setenv("AGENTSTACK_MAIL_WINDOW_ID", "11111111-1111-4111-8111-111111111111")
    config.clear_settings_cache()

    async def run():
        project, _, agent, archive = await _prepare(tmp_path)
        before, projects_before = await _rows()
        _forbid_enrollment(monkeypatch)
        monkeypatch.setattr(app, "_aware_utc", lambda: STAMP + timedelta(days=1))
        override = {} if attachments is None else {"attachments_policy": attachments}
        response = await _call(project, agent, **override)
        after, projects_after = await _rows()
        expected = {**before[0], "model": "new-model", "task_description": "new task",
                    "last_active_ts": (STAMP + timedelta(days=1)).isoformat().replace("+00:00", "Z")}
        assert after == [expected]
        assert projects_after == projects_before
        assert response["id"] == agent.id and response["name"] == agent.name
        assert response["project_id"] == project.id and response["program"] == agent.program
        assert response["model_raw"] == "new-model" and response["task_description"] == "new task"
        assert response["attachments_policy"] == "file" and response["retired_at"]
        profile = json.loads((archive.root / "agents" / agent.name / "profile.json").read_text())
        assert profile == {**response, "window_id": "existing-window", "window_display_name": "Existing window"}
        assert TOKEN not in json.dumps(response) and TOKEN not in json.dumps(profile)

    asyncio.run(run())


@pytest.mark.parametrize("case", [
    "missing_row", "null_owner", "empty_owner", "whitespace_owner", "wrong_token",
    "missing_token", "empty_token", "whitespace_token", "wrong_name", "alias_name",
    "missing_name", "wrong_project", "project_slug", "project_alias", "missing_project",
    "wrong_program", "missing_id", "zero_id", "negative_id", "bool_id", "float_id", "string_id",
    "string_refresh", "integer_refresh", "null_refresh",
])
def test_rejected_refresh_preserves_database_and_archive(isolated_runtime, monkeypatch, tmp_path, case):
    async def run():
        stored = {"null_owner": None, "empty_owner": "", "whitespace_owner": " \t "}.get(case, TOKEN)
        project, other, agent, archive = await _prepare(tmp_path, token=stored)
        before, files_before = await _rows(), _files(archive)
        _forbid_enrollment(monkeypatch)
        overrides = {
            "missing_row": {"existing_agent_id": agent.id + 100},
            "wrong_token": {"registration_token": "different-owner"},
            "missing_token": {"registration_token": None},
            "empty_token": {"registration_token": ""},
            "whitespace_token": {"registration_token": " \t "},
            "wrong_name": {"name": "DifferentResearcher"},
            "alias_name": {"name": "legacyresearcher"},
            "missing_name": {"name": None},
            "wrong_project": {"project_key": other.human_key},
            "project_slug": {"project_key": project.slug},
            "project_alias": {"project_key": project.human_key + "/../project"},
            "missing_project": {"project_key": str(tmp_path / "unknown")},
            "wrong_program": {"program": "codex"},
            "missing_id": {"existing_agent_id": None},
            "zero_id": {"existing_agent_id": 0},
            "negative_id": {"existing_agent_id": -1},
            "bool_id": {"existing_agent_id": True},
            "float_id": {"existing_agent_id": float(agent.id)},
            "string_id": {"existing_agent_id": str(agent.id)},
            "string_refresh": {"refresh_existing": "true"},
            "integer_refresh": {"refresh_existing": 1},
            "null_refresh": {"refresh_existing": None},
        }
        with pytest.raises((app.ToolExecutionError, ValidationError)):
            await _call(project, agent, **overrides.get(case, {}))
        assert await _rows() == before
        assert _files(archive) == files_before

    asyncio.run(run())


@pytest.mark.parametrize("refresh", [None, False])
def test_existing_owner_default_stays_authentication_only(isolated_runtime, monkeypatch, tmp_path, refresh):
    async def run():
        project, _, agent, archive = await _prepare(tmp_path)
        before, files_before = await _rows(), _files(archive)
        _forbid_enrollment(monkeypatch)
        server = app.build_mcp_server()
        tool = await server.get_tool("register_agent")
        arguments = dict(ctx=Context(server), project_key=project.human_key,
                         program=agent.program, name=agent.name, existing_agent_id=agent.id,
                         registration_token=TOKEN, model="attempted model",
                         task_description="attempted task", attachments_policy="inline")
        if refresh is not None:
            arguments["refresh_existing"] = refresh
        response = _payload(await tool.run(arguments))
        assert response["model_raw"] == "original-model"
        assert await _rows() == before
        assert _files(archive) == files_before

    asyncio.run(run())


@pytest.mark.parametrize("race", [
    "name", "program", "project_id", "token", "null_token", "empty_token", "generation",
    "inception", "project_key", "project_creation", "delete", "recreate_same_id", "recreate_new_id",
])
def test_refresh_rechecks_authority_atomically_after_preliminary_read(
    isolated_runtime, monkeypatch, tmp_path, race,
):
    async def run():
        project, other, agent, archive = await _prepare(tmp_path)
        original_refresh = app._refresh_existing_owned_agent
        files_before = _files(archive)
        after_race = None

        async def race_before_update(*args, **kwargs):
            nonlocal after_race
            async with db.get_session() as session:
                current = await session.get(Agent, agent.id)
                if race in {"delete", "recreate_same_id", "recreate_new_id"}:
                    await session.execute(delete(Agent).where(Agent.id == agent.id))
                    if race != "delete":
                        replacement = Agent(**{**agent.model_dump(),
                            "id": agent.id if race == "recreate_same_id" else agent.id + 1,
                            "inception_ts": STAMP + timedelta(seconds=1)})
                        session.add(replacement)
                elif race in {"project_key", "project_creation"}:
                    current_project = await session.get(Project, project.id)
                    if race == "project_key":
                        current_project.human_key = str(tmp_path / "renamed-project")
                    else:
                        current_project.created_at += timedelta(seconds=1)
                    session.add(current_project)
                else:
                    field, value = {
                        "name": ("name", "AnotherName"),
                        "program": ("program", "codex"),
                        "project_id": ("project_id", other.id),
                        "token": ("registration_token", "rotated-owner"),
                        "null_token": ("registration_token", None),
                        "empty_token": ("registration_token", ""),
                        "generation": ("credential_generation", agent.credential_generation + 1),
                        "inception": ("inception_ts", STAMP + timedelta(seconds=1)),
                    }[race]
                    setattr(current, field, value)
                    session.add(current)
                await session.commit()
            after_race = await _rows()
            return await original_refresh(*args, **kwargs)

        _forbid_enrollment(monkeypatch)
        monkeypatch.setattr(app, "_refresh_existing_owned_agent", race_before_update)
        with pytest.raises(app.ToolExecutionError, match="changed during refresh"):
            await _call(project, agent)
        assert after_race is not None and await _rows() == after_race
        assert _files(archive) == files_before

    asyncio.run(run())


@pytest.mark.parametrize("clock_offset", [-1, 0, 1])
def test_refresh_activity_never_moves_backwards(isolated_runtime, monkeypatch, tmp_path, clock_offset):
    async def run():
        project, _, agent, _ = await _prepare(tmp_path)
        monkeypatch.setattr(app, "_aware_utc", lambda: STAMP + timedelta(seconds=clock_offset))
        await _call(project, agent)
        async with db.get_session() as session:
            current = await session.get(Agent, agent.id)
            assert current.last_active_ts == STAMP + timedelta(seconds=max(clock_offset, 0))
            assert current.model == "new-model"

    asyncio.run(run())


def test_archive_failure_reports_committed_refresh(isolated_runtime, monkeypatch, tmp_path):
    async def run():
        project, _, agent, archive = await _prepare(tmp_path)
        before = _files(archive)
        writer = app.write_agent_profile

        async def fail_write(*_args, **_kwargs):
            raise OSError("archive fixture unavailable")

        monkeypatch.setattr(app, "write_agent_profile", fail_write)
        with pytest.raises(app.ToolExecutionError) as failure:
            await _call(project, agent)
        assert failure.value.error_type == "PROFILE_ARCHIVE_SYNC_FAILED"
        assert failure.value.data == {"agent_id": agent.id, "database_updated": True}
        rows, _ = await _rows()
        assert rows[0]["model"] == "new-model" and rows[0]["task_description"] == "new task"
        assert _files(archive) == before
        monkeypatch.setattr(app, "write_agent_profile", writer)
        response = await _call(project, agent)
        profile = json.loads((archive.root / "agents" / agent.name / "profile.json").read_text())
        assert profile["last_active_ts"] == response["last_active_ts"]
        assert profile["model_raw"] == "new-model"

    asyncio.run(run())


def test_refresh_activity_repairs_legacy_null_timestamp(isolated_runtime, monkeypatch, tmp_path):
    async def run():
        # Reproduce an old nullable column in this test's disposable database;
        # leave the application's current non-null model metadata unchanged.
        metadata = MetaData()
        Project.__table__.to_metadata(metadata)
        legacy = Agent.__table__.to_metadata(metadata)
        legacy.c.last_active_ts.nullable = True
        async with db.get_engine().begin() as connection:
            await connection.run_sync(metadata.create_all)
        project, _, agent, _ = await _prepare(tmp_path)
        async with db.get_session() as session:
            await session.execute(update(Agent).where(Agent.id == agent.id).values(last_active_ts=None))
            await session.commit()
        monkeypatch.setattr(app, "_aware_utc", lambda: STAMP)
        response = await _call(project, agent)
        assert response["last_active_ts"] == app._iso(STAMP)
        async with db.get_session() as session:
            current = await session.get(Agent, agent.id)
            assert current.last_active_ts == STAMP

    asyncio.run(run())


@pytest.mark.parametrize("race", ["delete", "recreate", "owner", "generation", "project_key"])
def test_archive_readback_rejects_changed_authority_after_committed_update(
    isolated_runtime, monkeypatch, tmp_path, race,
):
    async def run():
        project, _, agent, archive = await _prepare(tmp_path)
        files_before = _files(archive)
        ensure = app.ensure_archive
        after_race = None

        async def race_after_commit(*args, **kwargs):
            nonlocal after_race
            async with db.get_session() as session:
                current = await session.get(Agent, agent.id)
                assert current.model == "new-model"
                if race in {"delete", "recreate"}:
                    await session.execute(delete(Agent).where(Agent.id == agent.id))
                    if race == "recreate":
                        session.add(Agent(**{**agent.model_dump(), "inception_ts": STAMP + timedelta(seconds=1)}))
                elif race == "project_key":
                    changed = await session.get(Project, project.id)
                    changed.human_key = str(tmp_path / "new-project")
                    session.add(changed)
                else:
                    if race == "owner":
                        current.registration_token = "rotated-owner"
                    else:
                        current.credential_generation += 1
                    session.add(current)
                await session.commit()
            after_race = await _rows()
            return await ensure(*args, **kwargs)

        monkeypatch.setattr(app, "ensure_archive", race_after_commit)
        with pytest.raises(app.ToolExecutionError) as failure:
            await _call(project, agent)
        assert failure.value.error_type == "PROFILE_ARCHIVE_SYNC_FAILED"
        assert failure.value.data == {"agent_id": agent.id, "database_updated": True}
        assert after_race is not None and await _rows() == after_race
        assert _files(archive) == files_before

    asyncio.run(run())


def test_out_of_order_refresh_archives_latest_owned_profile(isolated_runtime, monkeypatch, tmp_path):
    async def run():
        project, _, agent, archive = await _prepare(tmp_path)
        ensure = app.ensure_archive
        second_response = None
        second_started = False

        async def second_refresh_before_first_archive(*args, **kwargs):
            nonlocal second_started, second_response
            if not second_started:
                second_started = True
                second_response = await _call(project, agent, model="newer-model", task_description="newer task")
            return await ensure(*args, **kwargs)

        monkeypatch.setattr(app, "ensure_archive", second_refresh_before_first_archive)
        first_response = await _call(project, agent)
        assert second_response is not None
        profile = json.loads((archive.root / "agents" / agent.name / "profile.json").read_text())
        assert profile["model_raw"] == "newer-model" and profile["task_description"] == "newer task"
        assert profile["last_active_ts"] == second_response["last_active_ts"]
        assert first_response == second_response
        async with db.get_session() as session:
            current = await session.get(Agent, agent.id)
            assert app._agent_to_dict(current) == first_response

    asyncio.run(run())


def test_refresh_preserves_activity_advanced_after_preliminary_read(isolated_runtime, monkeypatch, tmp_path):
    async def run():
        project, _, agent, _ = await _prepare(tmp_path)
        refresh = app._refresh_existing_owned_agent
        newer_activity = STAMP + timedelta(days=2)
        monkeypatch.setattr(app, "_aware_utc", lambda: STAMP + timedelta(days=1))

        async def advance_activity(*args, **kwargs):
            async with db.get_session() as session:
                await session.execute(update(Agent).where(Agent.id == agent.id)
                                      .values(last_active_ts=newer_activity))
                await session.commit()
            return await refresh(*args, **kwargs)

        monkeypatch.setattr(app, "_refresh_existing_owned_agent", advance_activity)
        response = await _call(project, agent)
        assert response["last_active_ts"] == app._iso(newer_activity)
        assert response["model_raw"] == "new-model" and response["task_description"] == "new task"

    asyncio.run(run())
