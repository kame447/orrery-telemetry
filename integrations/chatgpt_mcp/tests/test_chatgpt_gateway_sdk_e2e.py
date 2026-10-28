"""End to end with the official MCP Python SDK client over Streamable HTTP."""
from __future__ import annotations

import asyncio

import pytest

mcp_client = pytest.importorskip("mcp.client.streamable_http")
from mcp import ClientSession  # noqa: E402


async def _session(config, token, fn):
    url = f"http://127.0.0.1:{config.port}{config.mcp_path}"
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with mcp_client.streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def test_sdk_client_lists_and_calls_read_tools(make_gateway, keys):
    config, _ = make_gateway()

    async def run(session):
        tools = await session.list_tools()
        listing = await session.call_tool("orrery_list_agents", {"running_only": True})
        status = await session.call_tool("orrery_status", {})
        return tools, listing, status

    tools, listing, status = asyncio.run(_session(config, keys.mint(), run))
    assert {t.name for t in tools.tools} >= {"orrery_status", "orrery_list_agents"}
    assert listing.isError is False
    assert listing.structuredContent["agents"][0]["name"] == "WindyFermi"
    assert status.structuredContent["dashboard"]["reachable"] is True


def test_sdk_client_without_token_cannot_initialize(make_gateway):
    config, _ = make_gateway()

    async def run(session):
        return await session.list_tools()

    with pytest.raises(BaseException) as info:
        asyncio.run(asyncio.wait_for(_session(config, None, run), 20))

    def flatten(exc):
        yield exc
        for inner in getattr(exc, "exceptions", ()):
            yield from flatten(inner)
        if exc.__cause__ is not None:
            yield from flatten(exc.__cause__)

    assert any("401" in str(e) for e in flatten(info.value)), repr(info.value)


def test_sdk_client_spawn_preview_and_commit(make_gateway, keys, dashboard, tmp_path):
    config, _ = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(tmp_path)})

    async def run(session):
        p = await session.call_tool("orrery_spawn_preview", {"task": "check CI", "directory": "proj", "name": "SdkProbe"})
        pid = p.structuredContent["preview_id"]
        c = await session.call_tool("orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "sdk-probe-1"})
        s = await session.call_tool("orrery_spawn_status", {"name": "SdkProbe"})
        return p, c, s

    p, c, s = asyncio.run(_session(config, keys.mint(scope="orrery.read orrery.spawn"), run))
    assert p.isError is False and c.structuredContent["outcome"] == "accepted"
    assert len(dashboard.posts()) == 1
    assert s.isError is True  # fake dashboard keeps no record for a normal launch; reported, not invented
