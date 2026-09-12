"""Codex account quota adapter using the documented App Server API."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from queue import Empty, Queue
from typing import Any

from .base import QuotaBucket, QuotaSnapshot


class CodexQuotaProvider:
    provider_name = "codex"
    source_name = "codex-app-server"
    ttl_seconds = 120

    def __init__(
        self,
        command: str | None = None,
        *,
        timeout: float = 8.0,
        reader: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        configured = command or os.environ.get("AGENTSTACK_CODEX_BIN", "").strip()
        self.command = configured or shutil.which("codex") or "codex"
        self.timeout = timeout
        self._reader = reader or _read_app_server_rate_limits

    def read(self) -> QuotaSnapshot:
        observed_at = int(time.time())
        payload = self._reader(self.command, timeout=self.timeout)
        return parse_codex_rate_limits(payload, observed_at=observed_at)


def parse_codex_rate_limits(
    payload: Mapping[str, Any],
    *,
    observed_at: int,
) -> QuotaSnapshot:
    """Normalize windows by duration, never by primary/secondary position."""

    rate_limits = payload.get("rateLimits")
    by_limit_id = payload.get("rateLimitsByLimitId")
    candidates: list[tuple[str, Mapping[str, Any]]] = []

    if isinstance(by_limit_id, Mapping):
        for limit_id, value in by_limit_id.items():
            if isinstance(value, Mapping):
                candidates.append((str(limit_id), value))
    if isinstance(rate_limits, Mapping):
        limit_id = str(rate_limits.get("limitId") or "codex")
        # The keyed map is authoritative; the legacy view is only a fallback.
        if not any(key == limit_id for key, _ in candidates):
            candidates.append((limit_id, rate_limits))

    buckets: list[QuotaBucket] = []
    seen: set[tuple[str, int, int | None]] = set()
    for limit_id, record in candidates:
        for field in ("primary", "secondary"):
            window = record.get(field)
            if not isinstance(window, Mapping):
                continue
            duration_mins = _positive_int(window.get("windowDurationMins"))
            used = window.get("usedPercent")
            if duration_mins is None or used is None:
                continue
            resets_at = _optional_int(window.get("resetsAt"))
            key = (limit_id, duration_mins, resets_at)
            if key in seen:
                continue
            seen.add(key)
            label = _duration_label(duration_mins)
            if len(candidates) > 1:
                label = f"{record.get('limitName') or limit_id} · {label}"
            bucket_id = _safe_id(f"{limit_id}-{duration_mins}m")
            buckets.append(
                QuotaBucket.from_used(
                    id=bucket_id,
                    label=label,
                    scope="account",
                    used_percent=used,
                    window_seconds=duration_mins * 60,
                    resets_at=resets_at,
                    quality="exact",
                )
            )

    buckets.sort(key=lambda bucket: (bucket.window_seconds or 0, bucket.id))
    if not buckets:
        return QuotaSnapshot(
            provider="codex",
            source="codex-app-server",
            observed_at=observed_at,
            status="unavailable",
            reason="no_rate_limit_windows",
        )
    return QuotaSnapshot(
        provider="codex",
        source="codex-app-server",
        observed_at=observed_at,
        status="ok",
        buckets=tuple(buckets),
    )


def _read_app_server_rate_limits(command: str, *, timeout: float) -> dict[str, Any]:
    process = subprocess.Popen(
        [command, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None:
        process.terminate()
        raise RuntimeError("codex app-server did not expose stdio")

    # selectors cannot wait on subprocess pipes on Windows. A tiny reader
    # thread works on POSIX and Windows while the main thread keeps a bounded
    # deadline for the JSON-RPC exchange.
    responses: Queue[str | None] = Queue()
    reader = threading.Thread(
        target=_pump_stdout,
        args=(process.stdout, responses),
        name="codex-quota-stdout",
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        # Codex App Server uses JSON-RPC semantics but its documented stdio
        # wire format omits the conventional `jsonrpc: "2.0"` member.
        _write_message(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "orrery-telemetry",
                        "title": "ORRERY Telemetry",
                        "version": "1",
                    }
                },
            },
        )
        _read_response(responses, 1, deadline)
        _write_message(process, {"method": "initialized"})
        _write_message(
            process,
            {"id": 2, "method": "account/rateLimits/read"},
        )
        result = _read_response(responses, 2, deadline)
        if not isinstance(result, dict):
            raise RuntimeError("account/rateLimits/read returned a non-object result")
        return result
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        reader.join(timeout=1)
        if not reader.is_alive():
            process.stdout.close()


def _pump_stdout(stream: Any, responses: Queue[str | None]) -> None:
    try:
        for line in stream:
            responses.put(line)
    except (OSError, UnicodeError):
        # Treat invalid transport bytes as EOF. A background-thread traceback
        # could otherwise log fragments of a provider/account payload.
        pass
    finally:
        responses.put(None)


def _write_message(process: subprocess.Popen[str], message: Mapping[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(dict(message), separators=(",", ":")) + "\n")
    process.stdin.flush()


def _read_response(
    responses: Queue[str | None],
    request_id: int,
    deadline: float,
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"codex app-server timed out waiting for request {request_id}")
        try:
            line = responses.get(timeout=remaining)
        except Empty as exc:
            raise TimeoutError(
                f"codex app-server timed out waiting for request {request_id}"
            ) from exc
        if line is None:
            raise RuntimeError("codex app-server closed stdout")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            # Keep remote error text out of the browser-facing snapshot. The
            # service boundary exposes only a stable failure code.
            raise RuntimeError("codex app-server request failed")
        result = message.get("result", {})
        return result if isinstance(result, dict) else {}


def _duration_label(minutes: int) -> str:
    if minutes % 10080 == 0:
        weeks = minutes // 10080
        return "7d" if weeks == 1 else f"{weeks}w"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _positive_int(value: object) -> int | None:
    # JSON integer fields must not turn booleans/fractions into invented
    # durations. In particular, int(True) silently manufactures a 1m window.
    return value if type(value) is int and value > 0 else None


def _optional_int(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", value).strip("-") or "quota"
