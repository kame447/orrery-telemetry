"""Fork-only legacy create guard: no reuse, claim, rename or token exposure."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client
from sqlalchemy import select

from agentstack_mail import app, config, db
from agentstack_mail.models import Agent
from test_identity_contract import _configure_isolated_runtime, _payload


@pytest.mark.parametrize("existing_token", [None, "owner-of-existing-agent"])
def test_create_only_preserves_existing_identity(monkeypatch, tmp_path, existing_token):
    _configure_isolated_runtime(monkeypatch, tmp_path, mode="passthrough")
    project = str(tmp_path / "project")
    token = "synthetic-create-only-owner"

    async def run():
        async with Client(app.build_mcp_server()) as client:
            ensured = _payload(await client.call_tool("ensure_project", {"human_key": project}))
            async with db.get_session() as session:
                # Legacy alias with no owner (or a different owner) is NOT a new identity.
                agent = Agent(project_id=ensured["id"], name="Quartz-Tesla", program="fixture",
                              model="original", registration_token=existing_token)
                session.add(agent)
                await session.commit()

            async def rows():
                async with db.get_session() as session:
                    return [row.model_dump(mode="json") for row in (await session.execute(select(Agent))).scalars()]

            before = await rows()
            args = dict(project_key=project, name="QuartzTesla", program="claude-code",
                        model="fixture", registration_token=token, create_only=True)
            result = await client.call_tool("register_agent", args, raise_on_error=False)
            assert result.is_error
            assert await rows() == before
            result = await client.call_tool("register_agent", {**args, "name": "QuietCurie"})
            new = _payload(result)
            assert new["name"] == "QuietCurie" and new["project_id"] == ensured["id"]
            assert type(new["id"]) is int and new["inception_ts"]
            assert token not in json.dumps(new)
            after = await rows()
            assert len(after) == 2
            replay = await client.call_tool("register_agent", {**args, "name": "QuietCurie"}, raise_on_error=False)
            assert replay.is_error and await rows() == after
            for changes in ({"name": "Quiet-Curie"}, {"name": None}, {"registration_token": None},
                            {"existing_agent_id": new["id"]}):
                refused = await client.call_tool("register_agent", {**args, "name": "BrightBohr", **changes}, raise_on_error=False)
                assert refused.is_error
                assert await rows() == after

    try:
        asyncio.run(run())
    finally:
        asyncio.run(db.dispose_database_for_shutdown())
        db.reset_database_state()
        config.clear_settings_cache()


def test_concurrent_create_only_has_one_owner(monkeypatch, tmp_path):
    _configure_isolated_runtime(monkeypatch, tmp_path, mode="passthrough")
    project = str(tmp_path / "project")

    async def run():
        async with Client(app.build_mcp_server()) as client:
            await client.call_tool("ensure_project", {"human_key": project})
            args = dict(project_key=project, name="QuietCurie", program="claude-code",
                        model="fixture", create_only=True)
            tokens = ("synthetic-first-owner", "synthetic-second-owner")
            results = await asyncio.gather(*(client.call_tool("register_agent", {**args, "registration_token": token}, raise_on_error=False)
                                             for token in tokens))
            winners = [i for i, result in enumerate(results) if not result.is_error]
            assert len(winners) == 1
            winner = winners[0]
            async with db.get_session() as session:
                rows = (await session.execute(select(Agent))).scalars().all()
                assert len(rows) == 1 and rows[0].registration_token == tokens[winner]
                assert rows[0].id == _payload(results[winner])["id"]

    try:
        asyncio.run(run())
    finally:
        asyncio.run(db.dispose_database_for_shutdown())
        db.reset_database_state()
        config.clear_settings_cache()


@pytest.mark.parametrize("first_create_only,second_create_only", [(True, True), (True, False), (False, True)])
def test_concurrent_equivalent_names_cannot_create_two_owners(
    monkeypatch, tmp_path, first_create_only, second_create_only,
):
    _configure_isolated_runtime(monkeypatch, tmp_path, mode="passthrough")
    project = str(tmp_path / "project")

    async def run():
        async with Client(app.build_mcp_server()) as client:
            await client.call_tool("ensure_project", {"human_key": project})
            first_read = asyncio.Event()
            second_read = asyncio.Event()
            initial_reads = {}
            original_lookup = app._find_registration_equivalent_agent

            async def controlled_lookup(session, project_id, name):
                result = await original_lookup(session, project_id, name)
                if name not in initial_reads:
                    initial_reads[name] = result is None
                    if name == "QuietCurie":
                        first_read.set()
                        # Force both contenders to see an absent identity
                        # before either can insert. The locked recheck must
                        # reject the loser, regardless of acquisition order.
                        await asyncio.wait_for(second_read.wait(), timeout=10)
                    else:
                        second_read.set()
                return result

            monkeypatch.setattr(app, "_find_registration_equivalent_agent", controlled_lookup)
            args = dict(project_key=project, program="claude-code", model="fixture")
            first = asyncio.create_task(client.call_tool("register_agent", {
                **args, "name": "QuietCurie", "registration_token": "synthetic-first-owner",
                "create_only": first_create_only,
            }, raise_on_error=False))
            await asyncio.wait_for(first_read.wait(), timeout=10)
            second = asyncio.create_task(client.call_tool("register_agent", {
                **args, "name": "Quietcurie", "registration_token": "synthetic-second-owner",
                "create_only": second_create_only,
            }, raise_on_error=False))
            results = await asyncio.gather(first, second)
            assert initial_reads == {"QuietCurie": True, "Quietcurie": True}
            winners = [i for i, result in enumerate(results) if not result.is_error]
            assert len(winners) == 1
            winner = winners[0]
            async with db.get_session() as session:
                rows = (await session.execute(select(Agent))).scalars().all()
                assert len(rows) == 1
                assert rows[0].name == ("QuietCurie", "Quietcurie")[winner]
                assert rows[0].registration_token == ("synthetic-first-owner", "synthetic-second-owner")[winner]
                assert rows[0].id == _payload(results[winner])["id"]

    try:
        asyncio.run(run())
    finally:
        asyncio.run(db.dispose_database_for_shutdown())
        db.reset_database_state()
        config.clear_settings_cache()
