"""Build an isolated fresh MCP runtime that attests imports in the serving interpreter.

Operator-only preparation. Merely constructing a command never starts a service.
LaunchAgent installation, PID verification and authorization remain separate gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

from .fresh_import_attest import ImportPlan, PROBE, check as check_imports
from .fresh_release_plan import Plan


class RuntimeCommandError(ValueError):
    """A fixed nonsecret refusal code."""


@dataclass(frozen=True)
class RuntimeCommand:
    """Private host artifact. Do not expose argv or environment to MCP tools."""

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    requires_in_process_attestation: bool
    service_started: bool = False

    def env(self) -> dict[str, str]:
        return dict(self.environment)


# The existing offline probe validates pinned gateway/Mail source, the exact
# dependency bundle, the imported module origins and a second dependency
# snapshot. The only change is its final action: serve in that same process.
_PROBE_SUCCESS = (
    "print(json.dumps({'gateway_import':True,'mail_import':True,'receipt_contract':True,\n"
    "                  'import_origins_verified':True,'third_party_pinned':pin_manifest is not None}))"
)


def _server_source() -> str:
    original = PROBE.rstrip()
    if not original.endswith(_PROBE_SUCCESS):
        raise RuntimeCommandError("FRESH_BOOTSTRAP_PROBE_CONTRACT_CHANGED")
    verified = original[:-len(_PROBE_SUCCESS)]
    return verified + """
# No second interpreter or unverified import path after the checks above.
# The already imported gateway object is the server that will be started.
try:
    result = gateway.main(['serve'])
except BaseException:
    raise SystemExit(78) from None
raise SystemExit(result if type(result) is int else 78)
"""


def build_command(
    plan: Plan,
    dependency: ImportPlan,
    *,
    verify_release: Callable,
    read_release: Callable,
) -> RuntimeCommand:
    """Return a pinned command for a future explicitly approved service manager.

    Does not use a shell, launchctl, Popen, a live Mail server or existing agent.
    The child repeats all source/dependency checks before entering gateway.main,
    so the offline proof is not substituted for the eventual process check.
    """
    try:
        if dependency.dependency_manifest is None:
            raise ValueError("unapproved dependencies")
        evidence = check_imports(
            plan, dependency, verify_release=verify_release, read_release=read_release
        )
        if (type(evidence) is not dict
                or evidence.get("validated") is not True
                or evidence.get("third_party_pinned") is not True
                or evidence.get("import_origins_verified") is not True
                or evidence.get("receipt_contract") is not True):
            raise ValueError("insufficient import proof")

        from .fresh_dependency_attest import verify as verify_dependency
        digest = verify_dependency(
            dependency.dependency_manifest, dependency.site_packages
        )["site_packages_sha256"]
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid dependency digest")

        # -I ignores PYTHONPATH and user site; -S prevents .pth startup hooks;
        # -B suppresses bytecode writes. PROBE adds only verified code roots.
        args = (
            str(plan.python), "-I", "-S", "-B", "-c", _server_source(),
            str(plan.release_dir), plan.release_digest,
            str(dependency.mail_package), dependency.mail_digest,
            str(dependency.site_packages), str(dependency.dependency_manifest), digest,
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "PYTHONDONTWRITEBYTECODE": "1",
            "ORRERY_MCP_MODE": "fresh",
            "ORRERY_MCP_AUTH": "pairing",
            "ORRERY_MCP_ENABLE_MESSAGE": "1",
            "ORRERY_MCP_ENABLE_SPAWN": "0",
            "ORRERY_MCP_PORT": str(plan.port),
            "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
            "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(plan.policy_path),
            "ORRERY_MCP_DASHBOARD_URL": "http://127.0.0.1:8770",
        }
        # No token, user-supplied PYTHONPATH, project key, inherited HOME or
        # environment variable from a live agent can enter this command.
        return RuntimeCommand(
            argv=args, environment=tuple(sorted(env.items())),
            requires_in_process_attestation=True,
        )
    except RuntimeCommandError:
        raise
    except Exception:
        raise RuntimeCommandError("FRESH_BOOTSTRAP_PREPARATION_REFUSED") from None
