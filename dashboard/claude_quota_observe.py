#!/usr/bin/env python3
"""Capture Claude Code status-line quota fields into ORRERY runtime state.

This helper is intentionally opt-in. ORRERY does not overwrite an existing
Claude statusLine command. Operators who choose this helper receive a compact
status line while the quota fields are copied into dashboard runtime state.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys
import tempfile
import time


def _snapshot_path() -> pathlib.Path:
    explicit = os.environ.get("AGENTSTACK_CLAUDE_QUOTA_SNAPSHOT", "").strip()
    if explicit:
        return pathlib.Path(explicit).expanduser()
    runtime = os.environ.get("AGENTSTACK_RUNTIME_DIR", "").strip()
    if runtime:
        return pathlib.Path(runtime).expanduser() / "claude-quota.json"
    return pathlib.Path("~/.agentstack/runtime/claude-quota.json").expanduser()


def _replace_snapshot(temporary: pathlib.Path, target: pathlib.Path) -> None:
    """Atomically publish a snapshot, tolerating transient Windows sharing races."""

    delay = 0.005
    for attempt in range(8):
        try:
            os.replace(temporary, target)
            return
        except PermissionError:
            # Windows can briefly reject ReplaceFile/MoveFileEx when two status
            # line processes publish the same target concurrently. The source
            # file is unique, so retrying the atomic rename is safe. POSIX
            # PermissionError is not a transient sharing violation and should
            # still surface immediately.
            if os.name != "nt" or attempt == 7:
                raise
            time.sleep(delay)
            delay *= 2


def _write_snapshot(rate_limits: object) -> None:
    if not isinstance(rate_limits, dict):
        return
    target = _snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"observed_at": int(time.time()), "rate_limits": rate_limits}
    temporary: pathlib.Path | None = None
    try:
        # Multiple Claude sessions may invoke the status line concurrently.
        # A unique same-directory temp file keeps the final rename atomic and
        # avoids two observers racing over one shared `claude-quota.json.tmp`.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = pathlib.Path(handle.name)
            handle.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        _replace_snapshot(temporary, target)
        temporary = None
        try:
            target.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _remaining(window: object) -> float | None:
    if not isinstance(window, dict):
        return None
    if isinstance(window.get("used_percentage"), bool):
        return None
    try:
        used = float(window.get("used_percentage"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(used):
        return None
    return max(0.0, min(100.0, 100.0 - used))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0

    rate_limits = payload.get("rate_limits")
    _write_snapshot(rate_limits)

    model = payload.get("model")
    if isinstance(model, dict):
        model_name = str(model.get("display_name") or model.get("id") or "Claude")
    else:
        model_name = "Claude"
    parts = [f"[{model_name}]"]
    if isinstance(rate_limits, dict):
        five = _remaining(rate_limits.get("five_hour"))
        week = _remaining(rate_limits.get("seven_day"))
        if five is not None:
            parts.append(f"5h {five:.0f}% left")
        if week is not None:
            parts.append(f"7d {week:.0f}% left")
    print(" | ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
