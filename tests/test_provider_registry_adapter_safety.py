from __future__ import annotations

import pytest

from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
)


def _native(provider_id: str, program: str) -> ProviderSpec:
    return ProviderSpec(
        id=provider_id,
        label=provider_id,
        program=program,
        models=(f"{provider_id}-1",),
        default_model=f"{provider_id}-1",
        capabilities=ProviderCapabilities(),
        provider_key=provider_id,
        dispatch="native",
    )


def test_adapter_provider_requires_explicit_adapter_script() -> None:
    """A missing adapter must fail closed instead of falling back to Claude spawn."""
    with pytest.raises(ValueError, match="adapter script is required"):
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
            ),
            provider_key="future-vendor",
            dispatch="adapter",
        )


def test_registry_rejects_duplicate_program_ownership() -> None:
    registry = ProviderRegistry([_native("first", "shared-cli")])
    with pytest.raises(ValueError, match="provider program already registered: shared-cli"):
        registry.register(_native("second", "shared-cli"))
