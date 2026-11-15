"""Opt-in start of one staged fresh MCP LaunchAgent; no implicit rollback.

Not a public MCP tool. The operating user must explicitly authorize a digest
for this exact staged service. Caller supplies a trusted read-only job lookup;
unknown service state is never interpreted as unloaded.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import os
from pathlib import Path
import socket
import subprocess
from typing import Callable

from .fresh_attested_status import _snapshot, check_running


class FreshStartError(ValueError):
    """Fixed refusal code; never carries launchctl output or credentials."""


def _fixture_label(label: str) -> bool:
    """Reserve fixture operations for an exact fixture label or its dash suffix."""
    root = "org.agentstack.chatgpt-mcp-fresh.fixture"
    return type(label) is str and (label == root or label.startswith(root + "-"))


def _approval(digest: str) -> str:
    return sha256(b"orrery-fresh-fixture-launchd-start-v1\0" + digest.encode("ascii")).hexdigest()


def preview(plan, dependency, home: Path, *, verify_release: Callable,
            read_release: Callable) -> dict:
    """Read-only; doesn't run launchctl, bind sockets, or fetch a secret."""
    try:
        path, digest = _snapshot(plan, dependency, home, verify_release, read_release)
        return {"operation": "fresh_start_preview_only", "service_label": plan.label,
                "port": plan.port, "plist_filename": path.name,
                "start_approval_digest": _approval(digest),
                "service_started": False, "live_process_verified": False}
    except Exception:
        raise FreshStartError("FRESH_START_PREFLIGHT_REFUSED") from None


def _port_available(port: int) -> bool:
    """Negative availability test only; never claimed as a reservation."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _run(argv: list[str], runner: Callable) -> None:
    result = runner(argv, capture_output=True, text=True, timeout=15)
    if getattr(result, "returncode", None) != 0:
        raise ValueError("launchctl failed")


def start(plan, dependency, home: Path, approval_digest: str, *,
          verify_release: Callable, read_release: Callable,
          read_secret: Callable, probe: Callable,
          is_loaded: Callable[[str], bool],
          loaded_plist_path: Callable[[str], str],
          fixture_mode: bool = False,
          runner: Callable = subprocess.run,
          port_available: Callable[[int], bool] = _port_available) -> dict:
    """Bootstrap and kickstart an explicitly approved dedicated job only.

    On any uncertainty after bootstrap, leave the job untouched for manual
    inspection. Never automatically retry, bootout, kill, or change other jobs.
    The caller is responsible for an isolated identity and Mail instance.
    """
    try:
        if fixture_mode is not True or not _fixture_label(plan.label):
            raise FreshStartError("FRESH_START_FIXTURE_ONLY")
        path, digest = _snapshot(plan, dependency, home, verify_release, read_release)
        if (type(approval_digest) is not str
                or not hmac.compare_digest(approval_digest, _approval(digest))):
            raise FreshStartError("FRESH_START_APPROVAL_MISMATCH")
        if is_loaded(plan.label) is not False:
            raise FreshStartError("FRESH_START_LOADED_OR_UNKNOWN")
        if port_available(plan.port) is not True:
            raise FreshStartError("FRESH_START_PORT_OCCUPIED_OR_UNKNOWN")
        # Recheck exact staged bytes and source immediately before the mutation.
        _, fresh_digest = _snapshot(plan, dependency, home, verify_release, read_release)
        if fresh_digest != digest:
            raise FreshStartError("FRESH_START_SOURCE_CHANGED")
    except FreshStartError:
        raise
    except Exception:
        raise FreshStartError("FRESH_START_PREFLIGHT_REFUSED") from None

    # Once launchctl has been invoked, its result may be ambiguous even when
    # it raises or times out. Never clean up or retry based on this response.
    try:
        domain = f"gui/{os.getuid()}"
        _run(["/bin/launchctl", "bootstrap", domain, str(path)], runner)
        if (is_loaded(plan.label) is not True
                or loaded_plist_path(plan.label) != str(path)):
            raise ValueError("loaded job path not verifiable")
        _run(["/bin/launchctl", "kickstart", f"{domain}/{plan.label}"], runner)
        live = check_running(
            plan, dependency, home, verify_release=verify_release,
            read_release=read_release, read_secret=read_secret, probe=probe,
        )
        if type(live) is not dict or live.get("verified") is not True:
            raise ValueError("not verified")
        # Only report successful start after the real service attestation.
        return {"operation": "fresh_start_verified", "service_label": plan.label,
                "pid": live["pid"], "live_process_verified": True,
                "runtime_import_provenance_verified": False,
                "delivery_observed": False, "agent_receive_observed": False,
                "agent_processing_observed": False}
    except Exception:
        raise FreshStartError("FRESH_START_OUTCOME_UNKNOWN") from None
