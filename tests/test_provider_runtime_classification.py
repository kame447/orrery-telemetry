from __future__ import annotations

import dashboard.server as server
from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
)


def test_registered_runtime_provider_is_classified_as_live_agent():
    assert server.classify(
        "GeminiCurie", "agy", "", True, program="antigravity"
    ) == "agent"


def test_future_runtime_provider_does_not_need_core_name_branch(monkeypatch):
    future = ProviderSpec(
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
    monkeypatch.setattr(server, "PROVIDER_REGISTRY", ProviderRegistry([future]))

    assert server.classify(
        "FutureCurie", "future-cli", "", True, program="future-cli"
    ) == "agent"


def test_provider_without_runtime_capability_keeps_legacy_finished_state(monkeypatch):
    offline = ProviderSpec(
        id="offline-ai",
        label="Offline AI",
        program="offline-cli",
        models=("offline-1",),
        default_model="offline-1",
        capabilities=ProviderCapabilities(
            effort=False,
            mcp=False,
            resume=False,
            runtime=False,
            transcript=False,
            standalone=True,
            worktree_required=False,
            resources_required=False,
        ),
        provider_key="offline-vendor",
    )
    monkeypatch.setattr(server, "PROVIDER_REGISTRY", ProviderRegistry([offline]))

    assert server.classify(
        "OfflineCurie", "offline-cli", "", True, program="offline-cli"
    ) == "finished"
