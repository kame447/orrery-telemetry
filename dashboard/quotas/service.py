"""Quota provider orchestration with TTL caching and failure isolation."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from .base import QuotaSnapshot


LOGGER = logging.getLogger("agentstack.dashboard.quotas")


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
        names = [provider.provider_name for provider in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("quota provider names must be unique")
        self.default_ttl_seconds = max(1, int(default_ttl_seconds))
        # Stale lifetime is about the age of the last successful observation,
        # not about the cache refresh cadence. Keep it independently tunable.
        self.stale_seconds = max(1, int(stale_seconds))
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}
        self._last_success: dict[str, QuotaSnapshot] = {}
        # One lock per provider prevents duplicate CLI/App Server processes when
        # concurrent HTTP requests miss the same cache without serializing other
        # providers behind the slowest one.
        self._provider_locks = {name: threading.Lock() for name in names}

    def read_all(self) -> dict[str, object]:
        now = self._clock()
        if len(self.providers) <= 1:
            snapshots = [self._read_provider(provider, now) for provider in self.providers]
        else:
            # Provider refreshes are independent I/O. Parallelize cold misses so
            # one unhealthy CLI does not add its timeout to every other provider.
            with ThreadPoolExecutor(
                max_workers=len(self.providers),
                thread_name_prefix="quota-refresh",
            ) as pool:
                futures = [
                    pool.submit(self._read_provider, provider, now)
                    for provider in self.providers
                ]
                # Preserve configured provider order in the API response.
                snapshots = [future.result() for future in futures]
        return {
            "ts": int(now),
            "degraded": any(snapshot.status != "ok" for snapshot in snapshots),
            "providers": [snapshot.to_dict() for snapshot in snapshots],
        }

    def _read_provider(self, provider: QuotaProvider, now: float) -> QuotaSnapshot:
        name = provider.provider_name
        with self._provider_locks[name]:
            # Re-check the cache only after taking the provider lock. Another
            # request may have populated it while this request was waiting.
            ttl = max(1, int(getattr(provider, "ttl_seconds", self.default_ttl_seconds)))
            cached = self._cache.get(name)
            if cached is not None and now - cached.fetched_at < ttl:
                # A stale snapshot has a separate absolute lifetime based on the
                # successful observation it wraps. Do not let cache TTL extend it.
                if (
                    cached.snapshot.status != "stale"
                    or now - cached.snapshot.observed_at <= self.stale_seconds
                ):
                    return cached.snapshot

            try:
                snapshot = provider.read()
                if snapshot.provider != name:
                    raise ValueError(
                        f"provider returned snapshot for {snapshot.provider!r}, expected {name!r}"
                    )
            except Exception as exc:  # provider boundary: keep all other telemetry alive
                # Provider/CLI errors can contain account or authentication data.
                # Keep logs diagnostic without persisting arbitrary exception text.
                LOGGER.warning(
                    "quota provider %s failed (%s)",
                    name,
                    type(exc).__name__,
                )
                snapshot = self._failure_snapshot(provider, now)
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
    ) -> QuotaSnapshot:
        return self._stale_or_unavailable(provider, now, "provider_read_failed")

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
