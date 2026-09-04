from __future__ import annotations

import pytest

from dashboard.providers.registry import ProviderCapabilities, ProviderSpec


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
