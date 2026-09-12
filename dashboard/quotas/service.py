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
        if len(self.providers) <= 1:
            snapshots = [self._read_provider(provider) for provider in self.providers]
        else:
            # Provider refreshes are independent I/O. Parallelize cold misses so
            # one unhealthy CLI does not add its timeout to every other provider.
            with ThreadPoolExecutor(
                max_workers=len(self.providers),
                thread_name_prefix="quota-refresh",
            ) as pool:
                futures = [
                    pool.submit(self._read_provider, provider)
                    for provider in self.providers
                ]
                # Preserve configured provider order in the API response.
                snapshots = [future.result() for future in futures]
        now = self._clock()
        snapshots = [
            self._bounded_snapshot(provider, snapshot, now)
            for provider, snapshot in zip(self.providers, snapshots)
        ]
        return {
            "ts": int(now),
            "degraded": any(snapshot.status != "ok" for snapshot in snapshots),
            "providers": [self._snapshot_payload(snapshot, now) for snapshot in snapshots],
        }

    def _snapshot_payload(self, snapshot: QuotaSnapshot, now: float) -> dict[str, object]:
        payload = snapshot.to_dict()
        previous = self._last_success.get(snapshot.provider)
        has_observation = bool(snapshot.buckets) or snapshot.reason in {
            "observation_stale", "observation_expired",
        }
        observed = snapshot.observed_at if has_observation else (
            previous.observed_at if previous is not None else None
        )
        # Unlike legacy observed_at on an unavailable snapshot, these fields
        # distinguish real quota evidence from a completed refresh attempt.
        payload["last_observed_at"] = observed if observed is not None and observed <= now else None
        cached = self._cache.get(snapshot.provider)
        payload["checked_at"] = int(cached.fetched_at) if cached is not None else None
        return payload

    def _read_provider(self, provider: QuotaProvider) -> QuotaSnapshot:
        name = provider.provider_name
        lock = self._provider_locks[name]
        if not lock.acquire(blocking=False):
            # A slow refresh must not accumulate HTTP workers and executor
            # threads waiting for the same subprocess. Return bounded prior
            # evidence (or unavailable) immediately; the next poll can retry.
            return self._stale_or_unavailable(provider, self._clock(), "refresh_in_progress")
        try:
            now = self._clock()
            # Cache and last-success updates belong to the refresh owner.
            ttl = max(1, int(getattr(provider, "ttl_seconds", self.default_ttl_seconds)))
            cached = self._cache.get(name)
            if cached is not None and 0 <= now - cached.fetched_at < ttl:
                return self._bounded_snapshot(provider, cached.snapshot, now)

            try:
                snapshot = provider.read()
                if snapshot.provider != name:
                    raise ValueError(
                        f"provider returned snapshot for {snapshot.provider!r}, expected {name!r}"
                    )
                snapshot = self._bounded_snapshot(provider, snapshot, self._clock())
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
                        fallback=snapshot,
                    )

            now = self._clock()
            snapshot = self._bounded_snapshot(provider, snapshot, now)
            self._cache[name] = _CacheEntry(fetched_at=now, snapshot=snapshot)
            return snapshot
        finally:
            lock.release()

    def _bounded_snapshot(
        self, provider: QuotaProvider, snapshot: QuotaSnapshot, now: float
    ) -> QuotaSnapshot:
        max_age = min(self.stale_seconds, getattr(provider, "max_age_seconds", self.stale_seconds))
        age = now - snapshot.observed_at
        if snapshot.buckets and (age < 0 or age > max_age):
            return QuotaSnapshot(
                provider=provider.provider_name,
                source=provider.source_name,
                observed_at=snapshot.observed_at,
                status="unavailable",
                reason="observation_expired" if age >= 0 else "observation_in_future",
            )
        if snapshot.buckets and any(
            bucket.resets_at is not None and bucket.resets_at <= now
            for bucket in snapshot.buckets
        ):
            return snapshot.with_status("stale", "window_reset_pending")
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
        *,
        fallback: QuotaSnapshot | None = None,
    ) -> QuotaSnapshot:
        previous = self._last_success.get(provider.provider_name)
        # A restarted dashboard may only have an expired on-disk observation.
        # Preserve that evidence, including when it is newer than our cache.
        if fallback is not None and fallback.reason in {"observation_stale", "observation_expired"}:
            if previous is None or fallback.observed_at > previous.observed_at:
                return fallback
        if previous is not None:
            return self._bounded_snapshot(provider, previous.with_status("stale", reason), now)
        return QuotaSnapshot(
            provider=provider.provider_name,
            source=provider.source_name,
            observed_at=int(now),
            status="unavailable",
            reason=reason,
        )
