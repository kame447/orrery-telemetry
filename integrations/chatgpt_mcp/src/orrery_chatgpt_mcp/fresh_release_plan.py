"""Read-only preflight for a separate, operator-authorized fresh MCP release."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable


class PlanError(ValueError):
    """Fixed labels only: no credentials, local paths or policy contents."""


LABEL = re.compile(r"org\.agentstack\.chatgpt-mcp-fresh(?:\.[a-z0-9-]{1,32})?")
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
RESERVED_PORTS = frozenset({8765, 8770, 8781, 8790, 8791, 18765})
POLICY_KIND = "orrery-mcp-fresh-message-policy-v1"


def _private_file(path: Path) -> dict:
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ValueError
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        value = json.loads(raw, object_pairs_hook=unique)
        if type(value) is not dict:
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, UnicodeError):
        raise PlanError("FRESH_POLICY_UNVERIFIED") from None


def _private_directory(path: Path) -> None:
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError
    except (OSError, ValueError):
        raise PlanError("FRESH_STATE_UNVERIFIED") from None


@dataclass(frozen=True)
class Plan:
    python: Path
    release_dir: Path
    release_digest: str
    source_commit: str
    state_dir: Path
    policy_path: Path
    label: str
    port: int
    mail_instance_id: str
    sender_agent_id: int
    recipients: tuple[tuple[int, str, str], ...]
    # Explicit override permits isolated fixture Dashboards; default preserves
    # the legacy loopback Dashboard and requires no change to existing plans.
    dashboard_url: str = "http://127.0.0.1:8770"

    def __post_init__(self) -> None:
        if (not isinstance(self.label, str) or not LABEL.fullmatch(self.label)
                or type(self.port) is not int or not 1024 <= self.port <= 65535
                or self.port in RESERVED_PORTS):
            raise PlanError("FRESH_SERVICE_IDENTITY_INVALID")
        # Never permit an arbitrary network target, DNS alias, URL credentials,
        # URL path, or the MCP listener itself as the Dashboard endpoint.
        match = (re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{3,4})", self.dashboard_url)
                 if type(self.dashboard_url) is str else None)
        if (match is None or not 1024 <= int(match.group(1)) <= 65535
                or int(match.group(1)) == self.port):
            raise PlanError("FRESH_DASHBOARD_TARGET_INVALID")
        if (not isinstance(self.release_digest, str) or not HEX64.fullmatch(self.release_digest)
                or not isinstance(self.source_commit, str) or not HEX40.fullmatch(self.source_commit)):
            raise PlanError("FRESH_RELEASE_PIN_INVALID")
        if (not isinstance(self.mail_instance_id, str) or not HEX32.fullmatch(self.mail_instance_id)
                or type(self.sender_agent_id) is not int or self.sender_agent_id <= 0):
            raise PlanError("FRESH_AUTHORITY_INVALID")
        if not isinstance(self.recipients, tuple) or not 1 <= len(self.recipients) <= 16:
            raise PlanError("FRESH_AUTHORITY_INVALID")
        seen = set()
        for recipient in self.recipients:
            if (not isinstance(recipient, tuple) or len(recipient) != 3
                    or type(recipient[0]) is not int or recipient[0] <= 0
                    or not isinstance(recipient[1], str) or not NAME.fullmatch(recipient[1])
                    or recipient[2] != "to" or recipient[0] in seen):
                raise PlanError("FRESH_AUTHORITY_INVALID")
            seen.add(recipient[0])
        for path in (self.python, self.release_dir, self.state_dir, self.policy_path):
            if not isinstance(path, Path) or not path.is_absolute():
                raise PlanError("FRESH_PLAN_PATH_INVALID")


def inspect(plan: Plan, verify_release: Callable[[Path, str, str], bool],
            read_release: Callable[[Path], dict]) -> dict:
    """Do not install, start, stop, register, load secrets or write files."""
    _private_directory(plan.state_dir)
    _private_file(plan.state_dir / "pairing.json")
    policy = _private_file(plan.policy_path)
    expected = [{"target_agent_id": ident, "expected_target_name": name, "kind": kind}
                for ident, name, kind in plan.recipients]
    if (set(policy) != {"kind", "context_path", "mail_instance_id", "sender_agent_id", "recipients"}
            or policy.get("kind") != POLICY_KIND
            or policy.get("mail_instance_id") != plan.mail_instance_id
            or type(policy.get("sender_agent_id")) is not int
            or policy.get("sender_agent_id") != plan.sender_agent_id
            or policy.get("recipients") != expected
            or not isinstance(policy.get("context_path"), str)):
        raise PlanError("FRESH_AUTHORITY_UNVERIFIED")
    context = Path(policy["context_path"])
    if not context.is_absolute() or context.resolve() != context:
        raise PlanError("FRESH_AUTHORITY_UNVERIFIED")
    try:
        if (plan.python.resolve(strict=True) != plan.python or not plan.python.is_file()
                or not os.access(plan.python, os.X_OK)
                or plan.release_dir.resolve(strict=True) != plan.release_dir):
            raise ValueError
        record = read_release(plan.release_dir)
        if (not isinstance(record, dict) or record.get("digest") != plan.release_digest
                or record.get("source_commit") != plan.source_commit):
            raise ValueError
        if verify_release(plan.release_dir, plan.release_digest, str(plan.python)) is not True:
            raise ValueError
    except Exception:
        raise PlanError("FRESH_RELEASE_UNVERIFIED") from None
    return {"validated": True, "service_label": plan.label, "port": plan.port,
            "mail_instance_id": plan.mail_instance_id,
            "sender_agent_id": plan.sender_agent_id, "recipient_count": len(plan.recipients),
            "release_digest": plan.release_digest, "source_commit": plan.source_commit,
            "live_process_verified": False, "fresh_client_verified": False,
            "dependencies_verified": False, "operation": "preflight_only"}
