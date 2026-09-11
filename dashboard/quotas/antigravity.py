"""Antigravity quota adapter using the read-only headless /usage command."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import QuotaBucket, QuotaSnapshot


_MIN_SAFE_VERSION = (1, 1, 11)


class AntigravityQuotaProvider:
    provider_name = "antigravity"
    source_name = "agy-print-usage"
    ttl_seconds = 180

    def __init__(self, command: str | None = None, *, timeout: float = 15.0) -> None:
        configured = (
            command
            or os.environ.get("AGENTSTACK_GEMINI_BIN", "").strip()
            or os.environ.get("AGENTSTACK_ANTIGRAVITY_BIN", "").strip()
        )
        self.command = _resolve_command(configured or "agy")
        self.timeout = timeout

    def read(self) -> QuotaSnapshot:
        observed_at = int(time.time())
        version = _read_version(self.command)
        if version is None or version < _MIN_SAFE_VERSION:
            return QuotaSnapshot(
                provider=self.provider_name,
                source=self.source_name,
                observed_at=observed_at,
                status="unavailable",
                reason="agy_too_old_for_safe_print_usage",
            )
        process = subprocess.run(
            [self.command, "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "agy /usage failed").strip()
            raise RuntimeError(detail[:160])
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("agy /usage returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise RuntimeError("agy /usage returned a non-object payload")
        return parse_antigravity_usage(payload, observed_at=observed_at)


def parse_antigravity_usage(
    payload: Mapping[str, Any],
    *,
    observed_at: int,
) -> QuotaSnapshot:
    command = payload.get("command")
    data = command.get("data") if isinstance(command, Mapping) else None
    groups = data.get("groups") if isinstance(data, Mapping) else None
    if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
        return QuotaSnapshot(
            provider="antigravity",
            source="agy-print-usage",
            observed_at=observed_at,
            status="unavailable",
            reason="usage_groups_missing",
        )

    buckets: list[QuotaBucket] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        group_name = str(group.get("name") or "Antigravity")
        raw_buckets = group.get("buckets")
        if not isinstance(raw_buckets, Sequence) or isinstance(raw_buckets, (str, bytes)):
            continue
        for raw in raw_buckets:
            if not isinstance(raw, Mapping):
                continue
            fraction = _number(raw.get("remaining_fraction"))
            if fraction is None:
                continue
            remaining = fraction * 100.0 if fraction <= 1.0 else fraction
            bucket_id = str(raw.get("id") or raw.get("window") or "quota")
            raw_window = str(raw.get("window") or bucket_id)
            window_seconds = _window_seconds(raw_window)
            window_label = _window_label(raw_window, window_seconds)
            label = f"{group_name} · {window_label}" if group_name else window_label
            resets_at = _parse_reset(raw.get("reset_time"))
            buckets.append(
                QuotaBucket.from_remaining(
                    id=bucket_id,
                    label=label,
                    scope="account",
                    remaining_percent=remaining,
                    window_seconds=window_seconds,
                    resets_at=resets_at,
                    quality="exact",
                )
            )

    if not buckets:
        return QuotaSnapshot(
            provider="antigravity",
            source="agy-print-usage",
            observed_at=observed_at,
            status="unavailable",
            reason="no_active_quota_buckets",
        )
    return QuotaSnapshot(
        provider="antigravity",
        source="agy-print-usage",
        observed_at=observed_at,
        status="ok",
        buckets=tuple(buckets),
    )


def _resolve_command(configured: str) -> str:
    path = Path(configured).expanduser()
    if path.is_absolute() or "/" in configured:
        return str(path)
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    if configured == "agy":
        for candidate in (
            Path("~/.local/bin/agy").expanduser(),
            Path("/opt/homebrew/bin/agy"),
            Path("/usr/local/bin/agy"),
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return configured


def _read_version(command: str) -> tuple[int, int, int] | None:
    try:
        process = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = f"{process.stdout}\n{process.stderr}"
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _parse_reset(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _window_seconds(value: str) -> int | None:
    text = value.strip().lower()
    if text in {"weekly", "week", "7d", "seven_day", "seven-day"}:
        return 7 * 24 * 60 * 60
    match = re.search(r"(\d+(?:\.\d+)?)\s*([mhdw])", text)
    if not match:
        return None
    amount = float(match.group(1))
    scale = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    seconds = int(amount * scale)
    return seconds if seconds > 0 else None


def _window_label(raw: str, seconds: int | None) -> str:
    if seconds == 5 * 60 * 60:
        return "5h"
    if seconds == 7 * 24 * 60 * 60:
        return "7d"
    return raw.strip() or "quota"
