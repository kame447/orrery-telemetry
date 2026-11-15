"""Stage a pinned fresh MCP LaunchAgent without loading or changing a live job.

This is an operator-only bridge between the same-process attested runtime and
future launchctl lifecycle management. Existing plist files are never replaced.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import plistlib
import re
import tempfile
from typing import Callable

from .fresh_import_attest import ImportPlan
from .fresh_release_plan import Plan, _private_file
from .fresh_runtime_command import RuntimeCommand, build_command
from .fresh_service_stage import _destination


class AttestedStageError(ValueError):
    """A fixed error code without paths, policy or credentials."""


def _spec(plan: Plan, dependency: ImportPlan, command: RuntimeCommand) -> dict:
    env = command.env()
    expected_env = {
        "PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
        "PYTHONDONTWRITEBYTECODE": "1", "ORRERY_MCP_MODE": "fresh",
        "ORRERY_MCP_AUTH": "pairing", "ORRERY_MCP_ENABLE_MESSAGE": "1",
        "ORRERY_MCP_ENABLE_SPAWN": "0", "ORRERY_MCP_PORT": str(plan.port),
        "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
        "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(plan.policy_path),
        "ORRERY_MCP_DASHBOARD_URL": "http://127.0.0.1:8770",
    }
    if (type(command) is not RuntimeCommand
            or command.requires_in_process_attestation is not True
            or command.service_started is not False
            or len(command.argv) != 13
            or command.argv[:5] != (str(plan.python), "-I", "-S", "-B", "-c")
            or not isinstance(command.argv[5], str)
            or not command.argv[5]
            or command.argv[6:12] != (
                str(plan.release_dir), plan.release_digest,
                str(dependency.mail_package), dependency.mail_digest,
                str(dependency.site_packages), str(dependency.dependency_manifest),
            )
            or re.fullmatch(r"[0-9a-f]{64}", command.argv[12]) is None
            or type(command.environment) is not tuple
            or len(env) != len(command.environment)
            or env != expected_env):
        raise AttestedStageError("FRESH_ATTESTED_COMMAND_UNVERIFIED")
    return {
        "Label": plan.label,
        "ProgramArguments": list(command.argv),
        "EnvironmentVariables": env,
        # No implicit boot or automatic restart while a lifecycle manager is
        # not yet available. A later stage must check and explicitly authorize.
        "RunAtLoad": False,
        "KeepAlive": False,
        "Umask": 0o077,
    }


def _candidate(
    plan: Plan, dependency: ImportPlan, home: Path,
    verify_release: Callable, read_release: Callable,
) -> tuple[Path, bytes, str]:
    try:
        prior_policy = _private_file(plan.policy_path)
        command = build_command(
            plan, dependency,
            verify_release=verify_release, read_release=read_release,
        )
        if prior_policy != _private_file(plan.policy_path):
            raise AttestedStageError("FRESH_ATTESTED_POLICY_CHANGED")
        destination = _destination(home, plan.label)
        spec = _spec(plan, dependency, command)
        plist = plistlib.dumps(spec, fmt=plistlib.FMT_XML)
        authority = json.dumps(
            {"source_commit": plan.source_commit,
             "release_digest": plan.release_digest,
             "mail_instance_id": plan.mail_instance_id,
             "sender_agent_id": plan.sender_agent_id,
             "recipients": plan.recipients,
             "policy": prior_policy},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        digest = sha256(plist + sha256(authority).digest()).hexdigest()
        return destination, plist, digest
    except AttestedStageError:
        raise
    except Exception:
        raise AttestedStageError("FRESH_ATTESTED_STAGE_PREFLIGHT_REFUSED") from None


def preview(
    plan: Plan, dependency: ImportPlan, home: Path, *,
    verify_release: Callable, read_release: Callable,
) -> dict:
    """Offline review; never read launchctl, write a plist or start a process."""
    path, _, digest = _candidate(
        plan, dependency, home, verify_release, read_release,
    )
    return {
        "operation": "attested_preview_only",
        "service_label": plan.label,
        "port": plan.port,
        "plist_filename": path.name,
        "approval_digest": digest,
        "same_process_import_preflight": True,
        "launchd_loaded": None,
        "process_started": False,
        "live_process_verified": False,
    }


def stage(
    plan: Plan, dependency: ImportPlan, home: Path, approval_digest: str, *,
    verify_release: Callable, read_release: Callable,
    is_loaded: Callable[[str], bool],
) -> dict:
    """Atomically publish an inactive, non-overwriting dedicated plist."""
    path, payload, digest = _candidate(
        plan, dependency, home, verify_release, read_release,
    )
    if (type(approval_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", approval_digest) is None
            or approval_digest != digest):
        raise AttestedStageError("FRESH_ATTESTED_STAGE_APPROVAL_MISMATCH")
    try:
        try:
            loaded = is_loaded(plan.label)
        except Exception:
            raise AttestedStageError("FRESH_ATTESTED_STAGE_LOADED_OR_UNKNOWN") from None
        if loaded is not False:
            raise AttestedStageError("FRESH_ATTESTED_STAGE_LOADED_OR_UNKNOWN")
        if os.path.lexists(path):
            raise AttestedStageError("FRESH_ATTESTED_STAGE_TARGET_EXISTS")
        fd, temporary = tempfile.mkstemp(prefix=".fresh-attested-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Hardlink publication fails if a competing file already exists.
            os.link(temporary, path)
        finally:
            os.unlink(temporary)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except AttestedStageError:
        raise
    except Exception:
        # If publication succeeded but fsync failed, do NOT delete, retry, or
        # report success. An operator must inspect the existing file.
        raise AttestedStageError("FRESH_ATTESTED_STAGE_WRITE_UNCERTAIN") from None
    return {
        "operation": "attested_staged_inactive",
        "service_label": plan.label,
        "plist_filename": path.name,
        "approval_digest": digest,
        "same_process_import_preflight": True,
        "launchd_loaded": False,
        "process_started": False,
        "live_process_verified": False,
    }
