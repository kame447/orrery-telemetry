"""Gated spawn: preview, confirmation, idempotency and unknown outcomes."""
from __future__ import annotations

import pytest

from test_chatgpt_gateway_security import call, post, rpc

SPAWN = "orrery.read orrery.spawn"


@pytest.fixture
def spawn_gateway(make_gateway, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    config, _ = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(proj)})
    return config, str(proj)


def preview(config, token, **args):
    args.setdefault("task", "Summarise the README")
    args.setdefault("directory", "proj")
    return call(config, token, "orrery_spawn_preview", args)


def test_spawn_tools_listed_with_scopes(spawn_gateway, keys):
    config, _ = spawn_gateway
    _, _, body = post(config, rpc("tools/list"), token=keys.mint(scope=SPAWN))
    tools = {t["name"]: t for t in body["result"]["tools"]}
    assert tools["orrery_spawn_commit"]["annotations"]["readOnlyHint"] is False
    assert tools["orrery_spawn_commit"]["securitySchemes"] == [{"type": "oauth2", "scopes": ["orrery.spawn"]}]
    assert tools["orrery_spawn_preview"]["inputSchema"]["properties"]["directory"]["enum"] == ["proj"]
    assert "dir" not in tools["orrery_spawn_preview"]["inputSchema"]["properties"]


def test_read_scope_cannot_spawn_and_gets_scope_challenge(spawn_gateway, keys, dashboard):
    config, _ = spawn_gateway
    r = preview(config, keys.mint(scope="orrery.read"))
    assert r["isError"]
    assert r["structuredContent"]["error"]["code"] == "insufficient_scope"
    assert 'scope="orrery.spawn"' in r["_meta"]["mcp/www_authenticate"][0]
    assert dashboard.posts(real_only=False) == []


def test_preview_is_dry_run_and_commit_spawns_once(spawn_gateway, keys, dashboard):
    config, proj = spawn_gateway
    token = keys.mint(scope=SPAWN)
    p = preview(config, token)["structuredContent"]
    assert p["launch"]["name"] == "CuriousNoether" and p["launch"]["directory"] == "proj"
    dry = dashboard.posts(real_only=False)
    assert len(dry) == 1 and dry[0]["dry_run"] is True
    assert dashboard.posts() == []
    assert dry[0]["standalone"] is True and "parent" not in dry[0] and dry[0]["dir"] == proj
    assert set(dry[0]) <= {"standalone", "name", "dir", "provider", "task", "worktree", "async", "model", "dry_run"}

    first = call(config, token, "orrery_spawn_commit", {"preview_id": p["preview_id"], "idempotency_key": "launch-0001"})
    assert first["isError"] is False
    assert first["structuredContent"]["outcome"] == "accepted"
    assert len(dashboard.posts()) == 1

    again = call(config, token, "orrery_spawn_commit", {"preview_id": p["preview_id"], "idempotency_key": "launch-0001"})
    assert again["structuredContent"]["replayed"] is True
    assert again["structuredContent"]["outcome"] == "accepted"
    assert len(dashboard.posts()) == 1

    other_key = call(config, token, "orrery_spawn_commit", {"preview_id": p["preview_id"], "idempotency_key": "launch-0002"})
    assert other_key["structuredContent"]["error"]["code"] == "preview_consumed"
    assert len(dashboard.posts()) == 1
    # No other mutating dashboard route was ever touched.
    assert {p for m, p, _ in dashboard.requests if m == "POST"} == {"/api/spawn"}


def test_idempotency_key_bound_to_one_preview(spawn_gateway, keys, dashboard):
    config, _ = spawn_gateway
    token = keys.mint(scope=SPAWN)
    a = preview(config, token, name="AlphaOne")["structuredContent"]["preview_id"]
    b = preview(config, token, name="BetaTwo")["structuredContent"]["preview_id"]
    call(config, token, "orrery_spawn_commit", {"preview_id": a, "idempotency_key": "same-key-1"})
    r = call(config, token, "orrery_spawn_commit", {"preview_id": b, "idempotency_key": "same-key-1"})
    assert r["structuredContent"]["error"]["code"] == "idempotency_conflict"
    assert len(dashboard.posts()) == 1


def test_preview_cannot_be_committed_by_another_operator(spawn_gateway, keys, dashboard):
    config, _ = spawn_gateway
    pid = preview(config, keys.mint(scope=SPAWN))["structuredContent"]["preview_id"]
    r = call(config, keys.mint(sub="user|operator-2", scope=SPAWN), "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "other-op-1"})
    assert r["structuredContent"]["error"]["code"] == "preview_not_found"
    assert dashboard.posts() == []


def test_forged_or_expired_preview_is_refused(spawn_gateway, keys, dashboard, monkeypatch):
    import orrery_chatgpt_mcp.tools as tools

    config, _ = spawn_gateway
    token = keys.mint(scope=SPAWN)
    r = call(config, token, "orrery_spawn_commit", {"preview_id": "A" * 32, "idempotency_key": "forged-01"})
    assert r["structuredContent"]["error"]["code"] == "preview_not_found"
    monkeypatch.setattr(tools, "PREVIEW_TTL", -1)
    pid = preview(config, token)["structuredContent"]["preview_id"]
    r = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "expired-01"})
    assert r["structuredContent"]["error"]["code"] == "preview_not_found"
    assert dashboard.posts() == []


@pytest.mark.parametrize("args,code", [
    ({"directory": "/etc"}, "invalid_arguments"),
    ({"directory": "../proj"}, "invalid_arguments"),
    ({"dir": "/"}, "invalid_arguments"),
    ({"parent": "WindyFermi"}, "invalid_arguments"),
    ({"tools": {"mcp": ["x"]}, }, "invalid_arguments"),
    ({"provider": "bash"}, "invalid_arguments"),
    ({"model": "x; rm -rf /"}, "invalid_arguments"),
    ({"name": "Bad-Name"}, "invalid_arguments"),
    ({"task": ""}, "invalid_arguments"),
    ({"task": "x" * 4001}, "invalid_arguments"),
    ({"name": "WindyFermi"}, "name_unavailable"),
])
def test_preview_rejects_out_of_policy_arguments(spawn_gateway, keys, dashboard, args, code):
    config, _ = spawn_gateway
    r = preview(config, keys.mint(scope=SPAWN), **args)
    assert r["isError"] and r["structuredContent"]["error"]["code"] == code
    assert dashboard.posts(real_only=False) == []


def test_shell_metacharacters_are_passed_as_data_only(spawn_gateway, keys, dashboard):
    config, _ = spawn_gateway
    task = "$(touch /tmp/pwned) `id` ; rm -rf ~ && echo"
    p = preview(config, keys.mint(scope=SPAWN), task=task)["structuredContent"]
    assert dashboard.posts(real_only=False)[0]["task"] == task  # JSON field, never a shell string here
    assert p["launch"]["task_chars"] == len(task)


def test_unknown_outcome_is_recorded_and_reconciled_not_retried(spawn_gateway, keys, dashboard):
    config, _ = spawn_gateway
    token = keys.mint(scope=SPAWN)
    dashboard.spawn_mode = "drop"
    pid = preview(config, token, name="GhostLaunch")["structuredContent"]["preview_id"]
    r = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "unknown-01"})
    assert r["isError"] and r["structuredContent"]["error"]["code"] == "outcome_unknown"
    assert r["structuredContent"]["error"]["name"] == "GhostLaunch"
    assert len(dashboard.posts()) == 1

    dashboard.spawn_mode = "ok"
    again = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "unknown-01"})["structuredContent"]
    assert again["replayed"] is True and again["outcome"] == "unknown"
    assert again["reconciliation"]["state"] == "observed"
    assert again["reconciliation"]["launch"]["state"] == "launching"
    assert "LOG TAIL" not in str(again)
    assert len(dashboard.posts()) == 1  # never re-sent

    status = call(config, token, "orrery_spawn_status", {"name": "GhostLaunch"})["structuredContent"]["launch"]
    assert status["state"] == "launching" and "detail" not in status


def test_dashboard_failure_is_reported_with_retention_flags(spawn_gateway, keys, dashboard):
    config, _ = spawn_gateway
    token = keys.mint(scope=SPAWN)
    dashboard.spawn_mode = "fail"
    pid = preview(config, token)["structuredContent"]["preview_id"]
    r = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "fail-0001"})
    err = r["structuredContent"]["error"]
    assert err["code"] == "spawn_failed" and err["registration_retained"] is True
    again = call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "fail-0001"})
    assert again["structuredContent"]["outcome"] == "rejected"
    assert len(dashboard.posts()) == 1


def test_ledger_survives_gateway_restart(make_gateway, keys, dashboard, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    state = tmp_path / "shared-state"
    config, server = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(proj)}, state_dir=state)
    token = keys.mint(scope=SPAWN)
    pid = preview(config, token)["structuredContent"]["preview_id"]
    call(config, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "restart-01"})
    config2, _ = make_gateway(spawn_enabled=True, spawn_approval="chat", spawn_dirs={"proj": str(proj)}, state_dir=state)
    r = call(config2, token, "orrery_spawn_commit", {"preview_id": pid, "idempotency_key": "restart-01"})
    assert r["structuredContent"]["replayed"] is True
    assert len(dashboard.posts()) == 1
