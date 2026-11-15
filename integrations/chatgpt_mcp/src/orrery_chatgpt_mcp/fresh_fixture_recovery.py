"""Read-only reconciliation after an uncertain fresh fixture start or stop.

This never interprets an absent launchd job as authorization to retry an
operation. The caller provides trusted read-only launchd and PID lookups.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .fresh_attested_start import _fixture_label
from .fresh_attested_status import _snapshot, check_running
from .fresh_attested_stop import _pid_alive


class FreshRecoveryError(ValueError):
    """Non-sensitive refusal code; no OS output or secrets."""


def inspect(
    plan, dependency, home: Path, *,
    verify_release: Callable, read_release: Callable,
    read_secret: Callable, probe: Callable,
    is_loaded: Callable[[str], bool | None],
    loaded_plist_path: Callable[[str], str | None],
    fixture_mode: bool = False,
    previous_pid: int | None = None,
    pid_alive: Callable[[int], bool | None] = _pid_alive,
) -> dict:
    """Observe one fixture; never bootstrap, bootout, kill, or approve retries.

    `previous_pid` is the PID from an earlier trusted observation, if known.
    A missing PID or job is not evidence that a message was not delivered.
    """
    if fixture_mode is not True or not _fixture_label(plan.label):
        raise FreshRecoveryError("FRESH_RECOVERY_FIXTURE_ONLY")
    if previous_pid is not None and (type(previous_pid) is not int or previous_pid <= 1):
        raise FreshRecoveryError("FRESH_RECOVERY_INVALID_PREVIOUS_PID")
    try:
        path, digest = _snapshot(plan, dependency, home, verify_release, read_release)
        loaded = is_loaded(plan.label)
        if type(loaded) is not bool:
            raise FreshRecoveryError("FRESH_RECOVERY_JOB_UNKNOWN")
        if loaded:
            if loaded_plist_path(plan.label) != str(path):
                raise FreshRecoveryError("FRESH_RECOVERY_LOADED_SOURCE_UNVERIFIED")
            live = check_running(
                plan, dependency, home, verify_release=verify_release,
                read_release=read_release, read_secret=read_secret, probe=probe,
            )
            if (type(live) is not dict or live.get("verified") is not True
                    or type(live.get("pid")) is not int or live["pid"] <= 1):
                raise FreshRecoveryError("FRESH_RECOVERY_PROCESS_UNVERIFIED")
            result = {
                "state": "running_attested",
                "verified_pid": live["pid"],
                "same_as_previous_pid": previous_pid == live["pid"] if previous_pid is not None else None,
                "previous_pid_absent_observed": False,
            }
        else:
            if previous_pid is not None:
                absent = pid_alive(previous_pid)
                if absent is not False:
                    raise FreshRecoveryError("FRESH_RECOVERY_PREVIOUS_PID_UNVERIFIED")
            result = {
                "state": "job_unloaded_observed",
                "verified_pid": None,
                "same_as_previous_pid": None,
                "previous_pid_absent_observed": previous_pid is not None,
            }
        after_path, after_digest = _snapshot(
            plan, dependency, home, verify_release, read_release,
        )
        if after_path != path or after_digest != digest or is_loaded(plan.label) is not loaded:
            raise FreshRecoveryError("FRESH_RECOVERY_STATE_CHANGED")
        if loaded:
            if loaded_plist_path(plan.label) != str(path):
                raise FreshRecoveryError("FRESH_RECOVERY_STATE_CHANGED")
            # The job can be replaced under the same label between the first
            # live probe and the final plist checks. Attest the same PID again.
            confirmed = check_running(
                plan, dependency, home, verify_release=verify_release,
                read_release=read_release, read_secret=read_secret, probe=probe,
            )
            if (type(confirmed) is not dict or confirmed.get("verified") is not True
                    or type(confirmed.get("pid")) is not int
                    or confirmed["pid"] != result["verified_pid"]):
                raise FreshRecoveryError("FRESH_RECOVERY_STATE_CHANGED")
            final_path, final_digest = _snapshot(
                plan, dependency, home, verify_release, read_release,
            )
            if (final_path != path or final_digest != digest
                    or is_loaded(plan.label) is not True
                    or loaded_plist_path(plan.label) != str(path)):
                raise FreshRecoveryError("FRESH_RECOVERY_STATE_CHANGED")
        if not loaded and previous_pid is not None and pid_alive(previous_pid) is not False:
            raise FreshRecoveryError("FRESH_RECOVERY_STATE_CHANGED")
        return {
            "operation": "fresh_fixture_recovery_read_only",
            "service_label": plan.label,
            **result,
            "restart_authorized": False,
            "stop_authorized": False,
            "service_recovered": False,
            "mail_delivery_observed": False,
            "agent_receive_observed": False,
            "agent_processing_observed": False,
        }
    except FreshRecoveryError:
        raise
    except Exception:
        raise FreshRecoveryError("FRESH_RECOVERY_UNVERIFIED") from None
