"""Explicit, fail-closed unload of one verified fixture fresh MCP LaunchAgent.

Operator-only: no public MCP tool, automatic rollback, or restart. A result
that cannot be proven after invoking launchctl remains unknown.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import os
from pathlib import Path
import subprocess
from typing import Callable

from .fresh_attested_start import _fixture_label, _run
from .fresh_attested_status import _snapshot, check_running


class FreshStopError(ValueError):
    """Fixed refusal code without secrets, paths or command output."""


def _approval(digest: str, pid: int) -> str:
    return sha256(
        b"orrery-fresh-fixture-launchd-stop-v1\0"
        + bytes.fromhex(digest)
        + pid.to_bytes(8, "big")
    ).hexdigest()


def _pid_alive(pid: int) -> bool | None:
    """Read-only PID existence check. Unknown permissions must fail closed."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError):
        return None


def _verified_target(plan, dependency, home: Path, *,
                     verify_release: Callable, read_release: Callable,
                     read_secret: Callable, probe: Callable,
                     is_loaded: Callable, loaded_plist_path: Callable,
                     fixture_mode: bool) -> tuple[str, int]:
    if fixture_mode is not True or not _fixture_label(plan.label):
        raise FreshStopError("FRESH_STOP_FIXTURE_ONLY")
    path, digest = _snapshot(
        plan, dependency, home, verify_release, read_release,
    )
    if is_loaded(plan.label) is not True or loaded_plist_path(plan.label) != str(path):
        raise FreshStopError("FRESH_STOP_JOB_UNVERIFIED")
    live = check_running(
        plan, dependency, home, verify_release=verify_release,
        read_release=read_release, read_secret=read_secret, probe=probe,
    )
    if (type(live) is not dict or live.get("verified") is not True
            or type(live.get("pid")) is not int or live["pid"] <= 1):
        raise FreshStopError("FRESH_STOP_PROCESS_UNVERIFIED")
    after_path, after_digest = _snapshot(
        plan, dependency, home, verify_release, read_release,
    )
    if (after_path != path or after_digest != digest
            or is_loaded(plan.label) is not True
            or loaded_plist_path(plan.label) != str(path)):
        raise FreshStopError("FRESH_STOP_TARGET_CHANGED")
    return digest, live["pid"]


def preview(plan, dependency, home: Path, *,
            verify_release: Callable, read_release: Callable,
            read_secret: Callable, probe: Callable,
            is_loaded: Callable, loaded_plist_path: Callable,
            fixture_mode: bool = False) -> dict:
    """Read-only live attestation; does not unload or reserve the job."""
    try:
        digest, pid = _verified_target(
            plan, dependency, home, verify_release=verify_release,
            read_release=read_release, read_secret=read_secret, probe=probe,
            is_loaded=is_loaded, loaded_plist_path=loaded_plist_path,
            fixture_mode=fixture_mode,
        )
        return {
            "operation": "fresh_stop_preview_only",
            "service_label": plan.label,
            "verified_pid": pid,
            "stop_approval_digest": _approval(digest, pid),
            "job_unloaded": False,
            "process_exited": False,
        }
    except FreshStopError:
        raise
    except Exception:
        raise FreshStopError("FRESH_STOP_PREFLIGHT_REFUSED") from None


def stop(plan, dependency, home: Path, approval_digest: str, *,
         verify_release: Callable, read_release: Callable,
         read_secret: Callable, probe: Callable,
         is_loaded: Callable, loaded_plist_path: Callable,
         fixture_mode: bool = False,
         runner: Callable = subprocess.run,
         pid_alive: Callable[[int], bool | None] = _pid_alive) -> dict:
    """Unloads only an explicitly approved and currently attested fixture.

    The approval binds the exact staged source and process PID, not merely
    the label. No action on a non-running or unverifiable job is authorized.
    A failed/ambiguous bootout is never retried or compensated automatically.
    """
    try:
        digest, pid = _verified_target(
            plan, dependency, home, verify_release=verify_release,
            read_release=read_release, read_secret=read_secret, probe=probe,
            is_loaded=is_loaded, loaded_plist_path=loaded_plist_path,
            fixture_mode=fixture_mode,
        )
        if (type(approval_digest) is not str
                or not hmac.compare_digest(approval_digest, _approval(digest, pid))):
            raise FreshStopError("FRESH_STOP_APPROVAL_MISMATCH")
        # A PID that disappeared or cannot be inspected is not the approved job.
        if pid_alive(pid) is not True:
            raise FreshStopError("FRESH_STOP_PROCESS_CHANGED")
        fresh_digest, fresh_pid = _verified_target(
            plan, dependency, home, verify_release=verify_release,
            read_release=read_release, read_secret=read_secret, probe=probe,
            is_loaded=is_loaded, loaded_plist_path=loaded_plist_path,
            fixture_mode=fixture_mode,
        )
        if fresh_digest != digest or fresh_pid != pid:
            raise FreshStopError("FRESH_STOP_TARGET_CHANGED")
    except FreshStopError:
        raise
    except Exception:
        raise FreshStopError("FRESH_STOP_PREFLIGHT_REFUSED") from None

    try:
        _run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{plan.label}"], runner)
        if is_loaded(plan.label) is not False or pid_alive(pid) is not False:
            raise ValueError("job or previous PID not proven stopped")
        return {
            "operation": "fresh_stop_verified",
            "service_label": plan.label,
            "previous_pid": pid,
            "job_unloaded_verified": True,
            "previous_pid_exited_verified": True,
            "mail_delivery_observed": False,
            "agent_processing_observed": False,
            "automatic_restart_attempted": False,
        }
    except Exception:
        # bootout may already have succeeded: never retry or kill by PID.
        raise FreshStopError("FRESH_STOP_OUTCOME_UNKNOWN") from None
