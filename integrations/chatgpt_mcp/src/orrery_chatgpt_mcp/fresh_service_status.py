"""Read-only attestation of a dedicated fresh MCP LaunchAgent.

This module never installs, starts, stops or repairs a service. It binds a
verified release and fixed authority to the process that actually responds.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import stat
from typing import Callable


class StatusError(ValueError):
    """Fixed code only; no paths, tokens, credentials or response bodies."""


EXPECTED_ENV = frozenset({
    "PYTHONPATH", "ORRERY_MCP_MODE", "ORRERY_MCP_AUTH",
    "ORRERY_MCP_ENABLE_SPAWN", "ORRERY_MCP_ENABLE_MESSAGE",
    "ORRERY_MCP_PORT", "ORRERY_MCP_STATE_DIR",
    "ORRERY_MCP_FRESH_MESSAGE_POLICY", "ORRERY_MCP_DASHBOARD_URL",
})


def _read_plist(path: Path) -> dict:
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o022 or info.st_nlink != 1):
                raise ValueError
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError
        value = plistlib.loads(raw)
        if type(value) is not dict:
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
        raise StatusError("FRESH_SERVICE_PLIST_UNVERIFIED") from None


def _match_plist(plan: object, spec: dict) -> None:
    env = spec.get("EnvironmentVariables")
    required = {
        "PYTHONPATH": str(plan.release_dir),
        "ORRERY_MCP_MODE": "fresh",
        "ORRERY_MCP_AUTH": "pairing",
        "ORRERY_MCP_ENABLE_SPAWN": "0",
        "ORRERY_MCP_ENABLE_MESSAGE": "1",
        "ORRERY_MCP_PORT": str(plan.port),
        "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
        "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(plan.policy_path),
        "ORRERY_MCP_DASHBOARD_URL": plan.dashboard_url,
    }
    valid = (set(spec) == {"Label", "ProgramArguments", "EnvironmentVariables", "RunAtLoad", "KeepAlive", "Umask"}
             and spec.get("Label") == plan.label
             and spec.get("ProgramArguments") == [str(plan.python), "-m", "orrery_chatgpt_mcp.fresh_server", "serve"]
             and type(env) is dict and set(env) == EXPECTED_ENV and env == required
             and spec.get("RunAtLoad") is False and spec.get("KeepAlive") is False
             and type(spec.get("Umask")) is int and spec["Umask"] == 0o077
             and "Program" not in spec and "WatchPaths" not in spec
             and "StartInterval" not in spec and "StartCalendarInterval" not in spec)
    if not valid:
        raise StatusError("FRESH_SERVICE_LAUNCH_CONFIGURATION_MISMATCH")


def check_running(
    plan: object, plist_path: Path, *,
    verify_release: Callable, read_release: Callable,
    read_secret: Callable, probe: Callable,
) -> dict:
    """Check on-disk authority, the loaded job and its live MCP response.

    All callbacks are trusted host dependencies. In particular, probe must
    bind launchctl PID and the established TCP peer before sending the secret.
    """
    try:
        from .fresh_release_plan import inspect
        from .fresh_process_probe import Authority
        static = inspect(plan, verify_release, read_release)
        if static.get("validated") is not True:
            raise StatusError("FRESH_RELEASE_UNVERIFIED")
        _match_plist(plan, _read_plist(plist_path))
        secret = read_secret(plan.state_dir)
        if type(secret) is not str or re.fullmatch(r"[A-Za-z0-9_-]{43}", secret) is None:
            raise StatusError("FRESH_PAIRING_UNVERIFIED")
        authority = Authority(plan.mail_instance_id, plan.sender_agent_id, plan.recipients)
        live = probe(label=plan.label, plist_path=str(plist_path), port=plan.port,
                     pairing_secret=secret, expected=authority)
        if (type(live) is not dict or live.get("verified") is not True
                or type(live.get("pid")) is not int or live["pid"] <= 1
                or live.get("runtime_mode") != "fresh"
                or live.get("mail_instance_id") != plan.mail_instance_id
                or type(live.get("sender_agent_id")) is not int
                or live["sender_agent_id"] != plan.sender_agent_id
                or type(live.get("recipient_count")) is not int
                or live["recipient_count"] != len(plan.recipients)
                or live.get("message_enabled_on_server") is not True
                or live.get("spawn_enabled_on_server") is not False
                or live.get("tools_verified") != 6):
            raise StatusError("FRESH_LIVE_AUTHORITY_UNVERIFIED")
        return {
            "verified": True,
            "service_label": plan.label,
            "port": plan.port,
            "pid": live["pid"],
            "release_integrity_at_rest": True,
            "live_process_verified": True,
            "effective_authority_verified": True,
            "mail_instance_id": plan.mail_instance_id,
            "sender_agent_id": plan.sender_agent_id,
            "recipient_count": len(plan.recipients),
            "message_enabled_on_server": True,
            "spawn_enabled_on_server": False,
            "fresh_client_delivery_verified": False,
            "process_import_provenance_verified": False,
        }
    except StatusError:
        raise
    except Exception:
        raise StatusError("FRESH_SERVICE_STATUS_UNVERIFIED") from None
