"""Codex account quota adapter using the documented App Server API."""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import subprocess
import time
from collections.abc import Mapping
from typing import Any

from .base import QuotaBucket, QuotaSnapshot


class CodexQuotaProvider:
    provider_name = "codex"
    source_name = "codex-app-server"
    ttl_seconds = 120

    def __init__(self, command: str | None = None, *, timeout: float = 8.0) -> None:
        configured = command or os.environ.get("AGENTSTACK_CODEX_BIN", "").strip()
        self.command = configured or shutil.which("codex") or "codex"
        self.timeout = timeout

    def read(self) -> QuotaSnapshot:
        observed_at = int(time.time())
        payload = _read_app_server_rate_limits(self.command, timeout=self.timeout)
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

    if isinstance(rate_limits, Mapping):
        candidates.append((str(rate_limits.get("limitId") or "codex"), rate_limits))
    if isinstance(by_limit_id, Mapping):
        for limit_id, value in by_limit_id.items():
            if isinstance(value, Mapping):
                candidates.append((str(limit_id), value))

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
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None:
        process.terminate()
        raise RuntimeError("codex app-server did not expose stdio")

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        _write_message(
            process,
            {
                "jsonrpc": "2.0",
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
        _read_response(process, selector, 1, deadline)
        _write_message(process, {"jsonrpc": "2.0", "method": "initialized"})
        _write_message(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"},
        )
        result = _read_response(process, selector, 2, deadline)
        if not isinstance(result, dict):
            raise RuntimeError("account/rateLimits/read returned a non-object result")
        return result
    finally:
        selector.close()
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


def _write_message(process: subprocess.Popen[str], message: Mapping[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(dict(message), separators=(",", ":")) + "\n")
    process.stdin.flush()


def _read_response(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    request_id: int,
    deadline: float,
) -> dict[str, Any]:
    assert process.stdout is not None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"codex app-server timed out waiting for request {request_id}")
        if not selector.select(remaining):
            raise TimeoutError(f"codex app-server timed out waiting for request {request_id}")
        line = process.stdout.readline()
        if line == "":
            raise RuntimeError("codex app-server closed stdout")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            error = message.get("error")
            if isinstance(error, Mapping):
                detail = error.get("message") or json.dumps(dict(error), sort_keys=True)
            else:
                detail = str(error)
            raise RuntimeError(f"codex app-server request failed: {detail}")
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
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "-", value).strip("-") or "quota"
