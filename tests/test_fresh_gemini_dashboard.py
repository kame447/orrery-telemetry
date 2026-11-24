"""Fresh NEW AGENT uses projectless admission and never legacy handoffs."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dashboard import gemini_provider_runtime as gemini
from dashboard.fresh_backend import Backend
from agentstack_mail.storage_origin_contract import B1Error


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fresh_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTSTACK_RUNTIME_MODE", "fresh")
    monkeypatch.setenv("AGENTSTACK_FRESH_CLIENT_CONTEXT", str(tmp_path / "context.json"))
    monkeypatch.delenv("AGENTSTACK_CLIENT_CONFIG", raising=False)
    monkeypatch.delenv("AGENTSTACK_GLOBAL_SERVER_CONFIG", raising=False)
    monkeypatch.delenv("AGENTSTACK_GEMINI_MODELS", raising=False)
    monkeypatch.delenv("AGENTSTACK_GEMINI_BIN", raising=False)
    monkeypatch.setenv("AGENTSTACK_SPAWN_ROOTS", str(tmp_path))
    module_name = "fresh_gemini_dashboard_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "dashboard/server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        assert module.FRESH_MODE and not module.DB_PATH
        calls = []

        async def spawn(payload, launch_spec, roots):
            Backend.validate(payload, roots)
            calls.append((payload, launch_spec))
            return {"ok": True, "pending": True, "child_name": payload["name"]}

        backend = SimpleNamespace(root=ROOT, validate=Backend.validate, spawn=spawn)
        module._FRESH_BACKEND = backend
        module._codex_models = lambda: ["gpt-native-fixture"]
        module._suggest_any_spawn_name = lambda: "FreshGenerated"
        module.spawn_names_payload = lambda: {"providers": []}

        def forbidden(*args, **kwargs):
            pytest.fail("fresh Gemini must not enter a legacy registration or task handoff")

        monkeypatch.setattr(module, "_project_key", forbidden)
        monkeypatch.setattr(module, "_runtime_agent_token", forbidden)
        monkeypatch.setattr(module, "_mcp_call", forbidden)
        monkeypatch.setattr(gemini, "_write_task_file", forbidden)
        monkeypatch.setattr(gemini, "_preflight", forbidden)
        monkeypatch.setattr(gemini, "_discover_models", lambda: ())
        monkeypatch.setattr(gemini, "_which", lambda command, path: "/fixture/bin/" + command)
        gemini.install(module)
        work = tmp_path / "plain-work-folder"
        (work / "src").mkdir(parents=True)
        (work / "src/note.py").write_text("original\n")
        payload = dict(provider="gemini", name="FreshGemini", dir=str(work),
            task="Literal task `no-shell` $(no-eval)", model="gemini-4-hydrogen-high",
            effort="xhigh", resources="src/**")
        yield SimpleNamespace(module=module, backend=backend, calls=calls,
                              payload=payload, work=work)
    finally:
        sys.modules.pop(module_name, None)


@pytest.mark.parametrize("parent_fields", [
    {"standalone": True},
    {"parent": "Parent"},
    {"parent_ref": {"mail_instance_id": "ba7e29ad-56f8-4f80-a821-5a75bb7104bb", "mail_agent_id": 7}},
])
def test_fresh_adapter_uses_native_spec_without_legacy_identity_or_task_file(fresh_dashboard, parent_fields):
    env = fresh_dashboard
    payload = {**env.payload, **parent_fields}
    result = env.module.do_spawn(payload)
    assert result["ok"] and result["pending"], result
    [(received, spec)] = env.calls
    assert received["task"] == payload["task"]
    assert (spec.provider, spec.program, spec.model, spec.effort) == (
        "gemini", "antigravity", "gemini-4-hydrogen-xhigh", "xhigh")
    assert spec.standalone_supported and not spec.worktree_required
    assert not spec.task_mail and not spec.handoff_paths and not spec.launcher_env
    assert spec.script == str(ROOT / "bin/agentstack-runtime-client")
    assert not (env.work / ".git").exists()


def test_fresh_catalog_and_legacy_catalog_keep_distinct_isolation_policies(fresh_dashboard):
    [fresh] = fresh_dashboard.module.spawn_names_payload()["providers"]
    legacy = gemini._catalog_item(list(gemini._DEFAULT_MODELS))
    assert fresh["capabilities"]["standalone"] is True
    assert fresh["capabilities"]["worktree_required"] is False
    assert legacy["capabilities"]["standalone"] is False
    assert legacy["capabilities"]["worktree_required"] is True
    assert fresh["capabilities"]["resources_required"] is True
    assert fresh["effort_default"] == legacy["effort_default"] == ""


def test_fresh_adapter_generates_missing_name_and_normalizes_resource_lists(fresh_dashboard):
    env = fresh_dashboard
    payload = {**env.payload, "standalone": True, "resources": ["./src//**", "src/**"]}
    payload.pop("name")
    result = env.module.do_spawn(payload)
    assert result["child_name"] == "FreshGenerated", result
    [(received, _)] = env.calls
    assert received["resources"] == "src/**"
    assert "name" not in payload


@pytest.mark.parametrize("changes, error", [
    ({"worktree": True}, "FRESH_SPAWN_FEATURE_UNAVAILABLE"),
    ({"resources": ""}, "resources required for provider gemini"),
    ({"effort": ""}, "effort required for provider gemini"),
    ({"resources": "../outside"}, "resource must not contain parent traversal"),
    ({"task": ""}, "FRESH_CHILD_TASK_INVALID"),
])
def test_fresh_rejected_fields_never_reach_registration(fresh_dashboard, changes, error):
    env = fresh_dashboard
    result = env.module.do_spawn({**env.payload, "standalone": True, **changes})
    assert result["ok"] is False and error in result["error"], result
    assert not env.calls


def test_fresh_preflight_rejects_escaping_resources_and_missing_binary(fresh_dashboard, monkeypatch, tmp_path):
    env = fresh_dashboard
    outside = tmp_path / "outside"
    outside.mkdir()
    (env.work / "src/escape").symlink_to(outside, target_is_directory=True)
    result = env.module.do_spawn({**env.payload, "standalone": True})
    assert not result["ok"] and "symlink" in result["error"], result
    assert not env.calls
    (env.work / "src/escape").unlink()
    monkeypatch.setattr(gemini, "_which", lambda command, path: "" if command == "agy" else "/fixture/bin/" + command)
    result = env.module.do_spawn({**env.payload, "standalone": True})
    assert not result["ok"] and "Antigravity CLI not found" in result["error"], result
    assert not env.calls


def test_fresh_failure_stays_on_fresh_path_and_creates_no_task_file(fresh_dashboard):
    env = fresh_dashboard

    async def refused(*args):
        raise B1Error("FRESH_PARENT_CONTEXT_UNAVAILABLE")

    env.backend.spawn = refused
    result = env.module.do_spawn({**env.payload, "parent": "Missing"})
    assert result["error_code"] == "FRESH_PARENT_CONTEXT_UNAVAILABLE"
    assert not env.calls


def test_direct_fresh_backend_requires_antigravity_resources_before_preparation(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("empty-resource Antigravity spawn must not register an agent")

    monkeypatch.setattr("dashboard.fresh_backend.prepare", forbidden)
    backend = Backend.__new__(Backend)
    payload = dict(standalone=True, name="NeverRegistered", task="task", dir=str(tmp_path))
    with pytest.raises(B1Error) as error:
        asyncio.run(backend.spawn(payload, SimpleNamespace(program="antigravity"), [tmp_path]))
    assert error.value.code == "FRESH_CHILD_RESOURCES_REQUIRED"
