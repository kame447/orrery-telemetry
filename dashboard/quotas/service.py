"""Quota provider orchestration with TTL caching and failure isolation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .base import QuotaSnapshot


class QuotaProvider(Protocol):
    provider_name: str
    source_name: str
    ttl_seconds: int

    def read(self) -> QuotaSnapshot: ...


@dataclass(slots=True)
class _CacheEntry:
    fetched_at: float
    snapshot: QuotaSnapshot


class QuotaService:
    """Collect provider quota without coupling it to the hot /api/agents path."""

    def __init__(
        self,
        providers: list[QuotaProvider],
        *,
        default_ttl_seconds: int = 60,
        stale_seconds: int = 600,
        clock=time.time,
    ) -> None:
        self.providers = list(providers)
        self.default_ttl_seconds = max(1, int(default_ttl_seconds))
        self.stale_seconds = max(self.default_ttl_seconds, int(stale_seconds))
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}
        self._last_success: dict[str, QuotaSnapshot] = {}
        self._lock = threading.Lock()

    def read_all(self) -> dict[str, object]:
        # ThreadingHTTPServer may receive simultaneous /api/quotas requests.
        # Serialize this cold path so a cache miss starts each external provider
        # at most once instead of spawning duplicate CLI/App Server processes.
        with self._lock:
            now = self._clock()
            snapshots = [self._read_provider(provider, now) for provider in self.providers]
            return {
                "ts": int(now),
                "degraded": any(snapshot.status != "ok" for snapshot in snapshots),
                "providers": [snapshot.to_dict() for snapshot in snapshots],
            }

    def _read_provider(self, provider: QuotaProvider, now: float) -> QuotaSnapshot:
        name = provider.provider_name
        ttl = max(1, int(getattr(provider, "ttl_seconds", self.default_ttl_seconds)))
        cached = self._cache.get(name)
        if cached is not None and now - cached.fetched_at < ttl:
            return cached.snapshot

        try:
            snapshot = provider.read()
            if snapshot.provider != name:
                raise ValueError(
                    f"provider returned snapshot for {snapshot.provider!r}, expected {name!r}"
                )
        except Exception as exc:  # provider boundary: keep all other telemetry alive
            snapshot = self._failure_snapshot(provider, now, exc)
        else:
            if snapshot.status in {"ok", "degraded"} and snapshot.buckets:
                self._last_success[name] = snapshot
            elif snapshot.status == "unavailable":
                snapshot = self._stale_or_unavailable(
                    provider,
                    now,
                    snapshot.reason or "provider_unavailable",
                )

        self._cache[name] = _CacheEntry(fetched_at=now, snapshot=snapshot)
        return snapshot

    def _failure_snapshot(
        self,
        provider: QuotaProvider,
        now: float,
        exc: Exception,
    ) -> QuotaSnapshot:
        reason = f"{type(exc).__name__}: {exc}"[:160]
        return self._stale_or_unavailable(provider, now, reason)

    def _stale_or_unavailable(
        self,
        provider: QuotaProvider,
        now: float,
        reason: str,
    ) -> QuotaSnapshot:
        previous = self._last_success.get(provider.provider_name)
        if previous is not None and now - previous.observed_at <= self.stale_seconds:
            return previous.with_status("stale", reason)
        return QuotaSnapshot(
            provider=provider.provider_name,
            source=provider.source_name,
            observed_at=int(now),
            status="unavailable",
            reason=reason,
        )
