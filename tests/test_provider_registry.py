from __future__ import annotations

import dashboard.server as server
from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
    default_provider_registry,
)


def test_default_registry_exposes_builtin_providers_and_capabilities(monkeypatch):
    monkeypatch.setenv("AGENTSTACK_CODEX_MODELS", "gpt-test-a,gpt-test-b")
    registry = default_provider_registry()

    assert registry.ids() == ("claude", "codex", "gemini")

    claude = registry.require("claude")
    assert claude.program == "claude-code"
    assert claude.capabilities.effort is False
    assert claude.capabilities.resume is True
    assert claude.capabilities.worktree_required is False
    assert claude.capabilities.resources_required is False

    codex = registry.require("codex")
    assert codex.models == ("gpt-test-a", "gpt-test-b")
    assert codex.capabilities.effort is True
    assert codex.efforts == ("low", "medium", "high", "xhigh")

    gemini = registry.require("gemini")
    assert gemini.program == "antigravity"
    assert gemini.models[0] == "gemini-3.8-flash-high"
    assert gemini.capabilities.effort is True
    assert gemini.capabilities.mcp is True
    assert gemini.capabilities.worktree_required is True
    assert gemini.capabilities.resources_required is True
    assert gemini.capabilities.standalone is False


def test_catalog_is_generated_from_registry_not_provider_name_branches(monkeypatch):
    monkeypatch.setenv("AGENTSTACK_CODEX_MODELS", "gpt-test")
    registry = default_provider_registry()
    catalog = registry.catalog()

    assert [item["id"] for item in catalog] == ["claude", "codex", "gemini"]
    assert next(item for item in catalog if item["id"] == "gemini") == {
        "id": "gemini",
        "label": "Gemini",
        "program": "antigravity",
        "models": ["gemini-3.8-flash-high", "gemini-3.8-flash-medium"],
        "default_model": "gemini-3.8-flash-high",
        "efforts": ["low", "medium", "high"],
        "effort_default": "high",
        "capabilities": {
            "effort": True,
            "mcp": True,
            "resume": False,
            "runtime": True,
            "transcript": False,
            "standalone": False,
            "worktree_required": True,
            "resources_required": True,
        },
        "provider_key": "google",
    }


def test_new_provider_can_be_registered_without_editing_registry_core():
    registry = ProviderRegistry()
    registry.register(
        ProviderSpec(
            id="future-ai",
            label="Future AI",
            program="future-cli",
            models=("future-1",),
            default_model="future-1",
            capabilities=ProviderCapabilities(
                effort=False,
                mcp=True,
                resume=False,
                runtime=True,
                transcript=False,
                standalone=True,
                worktree_required=False,
                resources_required=False,
            ),
            provider_key="future-vendor",
        )
    )

    assert registry.ids() == ("future-ai",)
    assert registry.require("future-ai").program == "future-cli"
    assert registry.catalog()[0]["id"] == "future-ai"


def test_provider_validation_is_capability_driven():
    registry = default_provider_registry()

    assert registry.validate_request("claude", "claude-sonnet-5", "") == {
        "provider": "claude",
        "model": "claude-sonnet-5",
        "effort": "",
        "worktree_required": False,
        "resources_required": False,
    }
    assert registry.validate_request("codex", "gpt-5.6-sol", "high")["effort"] == "high"
    assert registry.validate_request("gemini", "gemini-3.8-flash-high", "medium") == {
        "provider": "gemini",
        "model": "gemini-3.8-flash-high",
        "effort": "medium",
        "worktree_required": True,
        "resources_required": True,
    }

    for args, message in [
        (("claude", "claude-sonnet-5", "high"), "effort not supported"),
        (("gemini", "gemini-3.8-flash-high", "xhigh"), "effort not allowed"),
        (("gemini", "unknown-model", "high"), "model not allowed"),
        (("unknown", "whatever", ""), "provider not allowed"),
    ]:
        try:
            registry.validate_request(*args)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"expected validation error for {args}")


def test_dashboard_spawn_catalog_comes_from_provider_registry(monkeypatch):
    monkeypatch.setenv("AGENTSTACK_CODEX_MODELS", "gpt-test")
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": "Sunny\n\036Curie\n"})(),
    )
    monkeypatch.setattr(
        server,
        "_spawn_scientist_statuses",
        lambda _adjectives, scientists: {name: "unknown" for name in scientists},
    )

    expected = default_provider_registry().catalog()
    assert server.spawn_names_payload()["providers"] == expected


def test_dashboard_dispatch_accepts_injected_provider_without_new_if_branch(monkeypatch, tmp_path):
    """The core spawn path must ask the registry, not enumerate provider ids."""
    fake = ProviderSpec(
        id="future-ai",
        label="Future AI",
        program="future-cli",
        models=("future-1",),
        default_model="future-1",
        capabilities=ProviderCapabilities(
            effort=False,
            mcp=True,
            resume=False,
            runtime=True,
            transcript=False,
            standalone=True,
            worktree_required=False,
            resources_required=False,
        ),
        provider_key="future-vendor",
        launch_args=("--future",),
    )
    registry = ProviderRegistry([fake])
    monkeypatch.setattr(server, "PROVIDER_REGISTRY", registry)

    launcher = tmp_path / "spawn_child.sh"
    launcher.write_text("#!/bin/bash\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(server, "SPAWN_SCRIPT", str(launcher))
    monkeypatch.setattr(server, "RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(server, "HERE", str(tmp_path))
    monkeypatch.setattr(server, "_project_key", lambda: "/project")
    monkeypatch.setattr(server, "_spawn_name_status", lambda _: "available")
    monkeypatch.setattr(
        server,
        "_mcp_call",
        lambda method, args, timeout=15: {
            "ok": True,
            "data": {"name": "FutureCurie", "registration_token": "tok"}
            if method == "register_agent" else {},
        },
    )
    launched = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda args, **kwargs: launched.append(args))
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )

    result = server.do_spawn({
        "standalone": True,
        "name": "FutureCurie",
        "task": "work",
        "dir": str(tmp_path),
        "provider": "future-ai",
        "model": "future-1",
    })

    assert result["ok"] is True
    assert result["provider"] == "future-ai"
    assert "--future" in launched[0]
