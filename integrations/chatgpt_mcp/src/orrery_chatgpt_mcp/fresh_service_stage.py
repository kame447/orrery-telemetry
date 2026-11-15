"""Stage an exact, inactive dedicated fresh LaunchAgent without changing a live job."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
from typing import Callable

from .fresh_release_plan import Plan, _private_file, inspect
from .fresh_service_status import _match_plist


class StageError(ValueError):
    """Fixed, nonsensitive errors at the local operator boundary."""


def _destination(home: Path, label: str) -> Path:
    """Only this user's existing, non-symlink LaunchAgents directory is writable."""
    try:
        if not home.is_absolute() or home.resolve(strict=True) != home:
            raise ValueError
        parent = home / "Library" / "LaunchAgents"
        if parent.resolve(strict=True) != parent:
            raise ValueError
        for directory in (home, parent.parent, parent):
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o022):
                raise ValueError
        return parent / (label + ".plist")
    except (OSError, ValueError, TypeError):
        raise StageError("FRESH_LAUNCH_DIRECTORY_UNVERIFIED") from None


def _spec(plan: Plan) -> dict:
    return {
        "Label": plan.label,
        "ProgramArguments": [str(plan.python), "-m", "orrery_chatgpt_mcp.fresh_server", "serve"],
        "EnvironmentVariables": {
            "PYTHONPATH": str(plan.release_dir),
            "ORRERY_MCP_MODE": "fresh",
            "ORRERY_MCP_AUTH": "pairing",
            "ORRERY_MCP_ENABLE_SPAWN": "0",
            "ORRERY_MCP_ENABLE_MESSAGE": "1",
            "ORRERY_MCP_PORT": str(plan.port),
            "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
            "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(plan.policy_path),
            "ORRERY_MCP_DASHBOARD_URL": "http://127.0.0.1:8770",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "Umask": 0o077,
    }


def _candidate(plan: Plan, home: Path, verify_release: Callable, read_release: Callable):
    first_policy = _private_file(plan.policy_path)
    proof = inspect(plan, verify_release, read_release)
    if proof.get("validated") is not True:
        raise StageError("FRESH_STATIC_PLAN_UNVERIFIED")
    if _private_file(plan.policy_path) != first_policy:
        raise StageError("FRESH_STATIC_PLAN_CHANGED")
    path = _destination(home, plan.label)
    spec = _spec(plan)
    _match_plist(plan, spec)
    payload = plistlib.dumps(spec, fmt=plistlib.FMT_XML)
    # The approval binds the pinned source and release, not just launchd arguments.
    authority = json.dumps({"instance": plan.mail_instance_id, "sender": plan.sender_agent_id,
                            "recipients": plan.recipients}, separators=(",", ":"), sort_keys=True)
    pinned_policy = json.dumps(first_policy, separators=(",", ":"), sort_keys=True,
                               ensure_ascii=False).encode("utf-8")
    digest = sha256(payload + plan.release_digest.encode("ascii")
                    + plan.source_commit.encode("ascii") + authority.encode("utf-8")
                    + sha256(pinned_policy).digest()).hexdigest()
    return path, payload, digest


def preview(plan: Plan, home: Path, *, verify_release: Callable, read_release: Callable) -> dict:
    """Preflight and render only; no filesystem write and no launchctl use."""
    try:
        path, _, digest = _candidate(plan, home, verify_release, read_release)
    except StageError:
        raise
    except Exception:
        raise StageError("FRESH_SERVICE_STAGE_PREFLIGHT_REFUSED") from None
    return {"operation": "preview_only", "service_label": plan.label, "port": plan.port,
            "approval_digest": digest, "plist_filename": path.name,
            "planned_release_digest": plan.release_digest,
            "launchd_loaded": None, "process_started": False, "dependencies_verified": False,
            "effective_authority_verified": False}


def stage(plan: Plan, home: Path, approval_digest: str, *, verify_release: Callable,
          read_release: Callable, is_loaded: Callable[[str], bool]) -> dict:
    """Write a new inactive plist exactly once. No activation or fallback."""
    try:
        path, payload, digest = _candidate(plan, home, verify_release, read_release)
        if (type(approval_digest) is not str or len(approval_digest) != 64
                or approval_digest != digest):
            raise StageError("FRESH_SERVICE_STAGE_APPROVAL_MISMATCH")
        if is_loaded(plan.label) is not False:
            raise StageError("FRESH_SERVICE_ALREADY_LOADED_OR_UNKNOWN")
        # No existing file, symlink or hardlink can be replaced, even with matching bytes.
        if os.path.lexists(path):
            raise StageError("FRESH_SERVICE_TARGET_ALREADY_EXISTS")
        fd, temp = tempfile.mkstemp(prefix=".fresh-plist-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic no-clobber publication. A racing creator wins; we fail closed.
            os.link(temp, path)
        finally:
            os.unlink(temp)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return {"operation": "staged_inactive", "service_label": plan.label,
                "plist_filename": path.name, "approval_digest": digest,
                "process_started": False, "launchd_loaded": False,
                "dependencies_verified": False, "effective_authority_verified": False}
    except StageError:
        raise
    except Exception:
        raise StageError("FRESH_SERVICE_STAGE_FAILED") from None
