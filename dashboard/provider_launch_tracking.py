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


def _forget(name: str) -> None:
    if not name:
        return
    with _TRACKING_LOCK:
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
        provider_id = str(payload.get("provider") or "").strip().lower()
        try:
            provider = base.PROVIDER_REGISTRY.require(provider_id)
        except ValueError:
            provider = None

        # A scientist name can be reused after a failed/retired launch. Never
        # let metadata from an earlier adapter launch bleed into the next one.
        requested_name = str(payload.get("name") or "").strip()
        if requested_name:
            _forget(requested_name)

        result = original_spawn(payload)
        child_name = str(result.get("child_name") or "").strip()
        if provider is None:
            if child_name:
                _forget(child_name)
            return result

        if provider.dispatch != "adapter":
            if child_name:
                _forget(child_name)
            return result

        if not (result.get("ok") and result.get("pending")):
            if child_name:
                _forget(child_name)
            return result
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
