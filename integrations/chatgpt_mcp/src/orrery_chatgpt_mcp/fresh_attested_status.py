"""Read-only proof of a *running* dedicated attested fresh LaunchAgent.

No loading, killing, repair or fallback is performed here. Matching a plist
alone never proves that its process is running or imported the pinned source.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Callable

from .fresh_attested_stage import _candidate
from .fresh_process_probe import Authority


class AttestedStatusError(ValueError):
    """Stable non-sensitive refusal code."""


def _snapshot(plan, dependency, home: Path, verify_release: Callable,
              read_release: Callable) -> tuple[Path, str]:
    """Recompute approval and compare the exact private on-disk plist bytes."""
    path, expected, digest = _candidate(
        plan, dependency, home, verify_release, read_release,
    )
    try:
        if len(expected) > 65536 or path.resolve(strict=True) != path:
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o777 != 0o600
                    or info.st_nlink != 1):
                raise ValueError
            actual = stream.read(65537)
        if actual != expected:
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise AttestedStatusError("FRESH_ATTESTED_PLIST_UNVERIFIED") from None
    return path, digest


def check_running(
    plan, dependency, home: Path, *,
    verify_release: Callable, read_release: Callable,
    read_secret: Callable, probe: Callable,
) -> dict:
    """Attest staged bytes, the launchctl PID, TCP peer and effective MCP grant.

    The trusted probe must bind the exact loaded job path and PID to the
    established loopback connection *before* sending the pairing secret.
    This does not prove in-process import provenance after a process starts.
    """
    try:
        path, digest = _snapshot(
            plan, dependency, home, verify_release, read_release,
        )
        secret = read_secret(plan.state_dir)
        if (type(secret) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", secret) is None):
            raise AttestedStatusError("FRESH_ATTESTED_PAIRING_UNVERIFIED")
        authority = Authority(plan.mail_instance_id, plan.sender_agent_id, plan.recipients)
        live = probe(
            label=plan.label, plist_path=str(path), port=plan.port,
            pairing_secret=secret, expected=authority,
        )
        if (type(live) is not dict
                or live.get("verified") is not True
                or type(live.get("pid")) is not int or live["pid"] <= 1
                or live.get("runtime_mode") != "fresh"
                or live.get("mail_instance_id") != plan.mail_instance_id
                or type(live.get("sender_agent_id")) is not int
                or live["sender_agent_id"] != plan.sender_agent_id
                or type(live.get("recipient_count")) is not int
                or live["recipient_count"] != len(plan.recipients)
                or live.get("message_enabled_on_server") is not True
                or live.get("spawn_enabled_on_server") is not False
                or type(live.get("tools_verified")) is not int
                or live["tools_verified"] != 6):
            raise AttestedStatusError("FRESH_ATTESTED_LIVE_AUTHORITY_UNVERIFIED")
        # Detect policy/plist or attestation manifest replacement while probing.
        _, after = _snapshot(
            plan, dependency, home, verify_release, read_release,
        )
        if after != digest:
            raise AttestedStatusError("FRESH_ATTESTED_SOURCE_CHANGED")
        return {
            "verified": True,
            "service_label": plan.label,
            "port": plan.port,
            "pid": live["pid"],
            "release_at_rest_verified": True,
            "staged_plist_verified": True,
            "effective_authority_verified": True,
            "mail_instance_id": plan.mail_instance_id,
            "sender_agent_id": plan.sender_agent_id,
            "recipient_count": len(plan.recipients),
            "message_enabled_on_server": True,
            "spawn_enabled_on_server": False,
            "mcp_tools_verified": 6,
            "runtime_import_provenance_verified": False,
            "delivery_observed": False,
            "agent_receive_observed": False,
            "agent_processing_observed": False,
        }
    except AttestedStatusError:
        raise
    except Exception:
        raise AttestedStatusError("FRESH_ATTESTED_STATUS_UNVERIFIED") from None
