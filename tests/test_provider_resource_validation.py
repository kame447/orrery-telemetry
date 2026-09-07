from __future__ import annotations

import dashboard.provider_runtime as provider_runtime
from dashboard.providers.registry import default_provider_registry


def test_required_resources_reject_delimiter_only_values() -> None:
    registry = default_provider_registry()
    provider = registry.require("gemini")

    for raw in (",", " , ", ",,,", "  ,  ,  "):
        try:
            provider_runtime._normalize_resources(provider, raw)
        except ValueError as exc:
            assert "resources required" in str(exc)
        else:
            raise AssertionError(f"delimiter-only resources were accepted: {raw!r}")


def test_resources_are_normalized_before_adapter_handoff() -> None:
    registry = default_provider_registry()
    provider = registry.require("gemini")
    assert provider_runtime._normalize_resources(
        provider, " src/** , tests/** ,, docs/README.md "
    ) == "src/**,tests/**,docs/README.md"
