from __future__ import annotations

from dashboard.provider_runtime import _inject_ui_capabilities
from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
)


def test_provider_logo_keys_are_json_quoted_for_javascript() -> None:
    provider = ProviderSpec(
        id="future-ai",
        label="Future AI",
        program="future-cli",
        models=("future-1",),
        default_model="future-1",
        capabilities=ProviderCapabilities(),
        provider_key="future-vendor",
        dispatch="native",
    )
    registry = ProviderRegistry([provider])
    source = """<script>
const _PROVIDER_ASPECT = {
  anthropic: 1,
};
</script>
"""

    rendered = _inject_ui_capabilities(source, registry)
    assert '"future-vendor": 1,' in rendered
    assert "  future-vendor: 1," not in rendered
