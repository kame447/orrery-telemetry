from __future__ import annotations

from types import SimpleNamespace

from dashboard import provider_launch_tracking
from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
)


def _provider(provider_id: str, dispatch: str) -> ProviderSpec:
    return ProviderSpec(
        id=provider_id,
        label=provider_id,
        program=f"{provider_id}-cli",
        models=(f"{provider_id}-1",),
        default_model=f"{provider_id}-1",
        capabilities=ProviderCapabilities(),
        provider_key=provider_id,
        dispatch=dispatch,
        adapter_script="spawn-provider.sh" if dispatch == "adapter" else "",
    )


def test_native_reuse_of_child_name_clears_stale_adapter_metadata() -> None:
    adapter = _provider("adapter-ai", "adapter")
    native = _provider("native-ai", "native")
    registry = ProviderRegistry([adapter, native])

    statuses = {
        "SharedCurie": {
            "ok": True,
            "state": "ready",
            "result": {"provider": "native-ai", "model": "native-ai-1"},
        }
    }
    calls = []

    def do_spawn(payload: dict) -> dict:
        calls.append(dict(payload))
        return {
            "ok": True,
            "pending": bool(payload.get("async")),
            "child_name": "SharedCurie",
            "provider": payload["provider"],
            "model": payload["model"],
        }

    base = SimpleNamespace(
        PROVIDER_REGISTRY=registry,
        do_spawn=do_spawn,
        spawn_launch_status=lambda name: dict(statuses[name]),
        _SPAWN_LAUNCH_RETENTION=1800.0,
    )
    provider_launch_tracking.install(base)

    first = base.do_spawn(
        {
            "provider": "adapter-ai",
            "model": "adapter-ai-1",
            "async": True,
            "name": "SharedCurie",
        }
    )
    assert first["pending"] is True

    second = base.do_spawn(
        {
            "provider": "native-ai",
            "model": "native-ai-1",
            "async": False,
            "name": "SharedCurie",
        }
    )
    assert second["provider"] == "native-ai"

    status = base.spawn_launch_status("SharedCurie")
    assert status["result"]["provider"] == "native-ai"
    assert status["result"]["model"] == "native-ai-1"
