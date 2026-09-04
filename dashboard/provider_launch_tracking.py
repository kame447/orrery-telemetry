"""Preserve provider metadata across asynchronous adapter launches.

The legacy dashboard core records the final async launch result from inside its
native Claude/Codex spawn path. Adapter providers temporarily reuse that path,
so the background verdict can otherwise expose the translated provider name.
This module overlays the provider metadata selected by the registry when status
is read, without changing the legacy launch lifecycle.
"""
from __future__ import annotations

import threading
import time
from typing import Any


_TRACKING_LOCK = threading.Lock()
_TRACKING: dict[str, dict[str, Any]] = {}


def _retention(base: Any) -> float:
    try:
        value = float(getattr(base, "_SPAWN_LAUNCH_RETENTION", 1800.0))
    except (TypeError, ValueError):
        return 1800.0
    return value if value > 0 else 1800.0


def _prune(base: Any, now: float) -> None:
    retention = _retention(base)
    stale = [
        name
        for name, metadata in _TRACKING.items()
        if now - float(metadata.get("ts", 0.0)) > retention
    ]
    for name in stale:
        _TRACKING.pop(name, None)


def _record(base: Any, name: str, *, provider: str, model: str, effort: str | None) -> None:
    now = time.time()
    with _TRACKING_LOCK:
        _prune(base, now)
        _TRACKING[name] = {
            "ts": now,
            "provider": provider,
            "model": model,
            "effort": effort,
        }


def _metadata(base: Any, name: str) -> dict[str, Any] | None:
    now = time.time()
    with _TRACKING_LOCK:
        _prune(base, now)
        metadata = _TRACKING.get(name)
        return dict(metadata) if metadata is not None else None


def install(base: Any) -> Any:
    """Install provider-aware async launch status overlays once."""
    if getattr(base, "_PROVIDER_LAUNCH_TRACKING_INSTALLED", False):
        return base

    original_spawn = base.do_spawn
    original_status = base.spawn_launch_status

    def do_spawn(payload: dict) -> dict:
        result = original_spawn(payload)
        if not (result.get("ok") and result.get("pending")):
            return result

        provider_id = str(result.get("provider") or payload.get("provider") or "").strip().lower()
        try:
            provider = base.PROVIDER_REGISTRY.require(provider_id)
        except ValueError:
            return result
        if provider.dispatch != "adapter":
            return result

        child_name = str(result.get("child_name") or "").strip()
        if not child_name:
            return result
        effort = result.get("effort")
        _record(
            base,
            child_name,
            provider=provider.id,
            model=str(result.get("model") or provider.resolved_default_model()),
            effort=str(effort) if effort is not None else None,
        )
        return result

    def spawn_launch_status(name: str) -> dict:
        status = original_status(name)
        metadata = _metadata(base, name)
        if metadata is None or not status.get("ok"):
            return status
        result = status.get("result")
        if not isinstance(result, dict):
            return status

        patched_result = dict(result)
        patched_result.update(
            provider=metadata["provider"],
            model=metadata["model"],
            effort=metadata["effort"],
        )
        patched_status = dict(status)
        patched_status["result"] = patched_result
        return patched_status

    base.do_spawn = do_spawn
    base.spawn_launch_status = spawn_launch_status
    base._PROVIDER_LAUNCH_TRACKING_INSTALLED = True
    return base
