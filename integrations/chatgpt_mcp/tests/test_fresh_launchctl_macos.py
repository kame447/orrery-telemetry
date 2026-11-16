"""Opt-in real macOS launchd lifecycle smoke; never starts the MCP or Mail."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MCP_LAUNCHD_E2E") != "1",
    reason="requires an explicit opt-in on an interactive macOS login session",
)


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args], capture_output=True, text=True, timeout=15,
        check=False,
    )


def _loaded_path(output: str) -> str | None:
    match = re.search(r"(?m)^\s*path = (.+)$", output)
    return match.group(1) if match else None


def _live_pid(output: str) -> int | None:
    match = re.search(r"(?m)^\s*pid = ([0-9]+)$", output)
    return int(match.group(1)) if match else None


def _pid_exists(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def test_dedicated_launchagent_bootstrap_kickstart_bootout(tmp_path: Path) -> None:
    """One unique inert job; prove launchctl behavior without touching production."""
    label = "org.agentstack.chatgpt-mcp-fresh.fixture-smoke-" + uuid4().hex[:16]
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, mode=0o700)
    plist_path = launch_agents / f"{label}.plist"
    definition = {
        "Label": label,
        "ProgramArguments": [sys.executable, "-c", "import time; time.sleep(90)"],
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    with plist_path.open("wb") as stream:
        plistlib.dump(definition, stream)
    plist_path.chmod(0o600)
    assert _launchctl("print", target).returncode != 0

    bootstrapped = False
    previous_pid = None
    cleanup = None
    try:
        # A timeout or failure after invocation is inconclusive; do not retry.
        bootstrapped = True
        loaded = _launchctl("bootstrap", domain, str(plist_path))
        assert loaded.returncode == 0, "isolated launchctl bootstrap failed"
        initial = _launchctl("print", target)
        assert initial.returncode == 0
        assert _loaded_path(initial.stdout) == str(plist_path.resolve())
        assert "type = LaunchAgent" in initial.stdout
        assert "state = not running" in initial.stdout
        started = _launchctl("kickstart", target)
        assert started.returncode == 0, "isolated launchctl kickstart failed"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            observed = _launchctl("print", target)
            if observed.returncode == 0 and _live_pid(observed.stdout) is not None:
                previous_pid = _live_pid(observed.stdout)
                assert _loaded_path(observed.stdout) == str(plist_path.resolve())
                assert "state = running" in observed.stdout
                break
            time.sleep(0.1)
        assert previous_pid is not None, "isolated launchctl job PID not observed"
        assert _pid_exists(previous_pid) is True
    finally:
        if bootstrapped:
            # Exactly one teardown for this random fixture label, never other jobs.
            cleanup = _launchctl("bootout", target)

    assert cleanup is not None and cleanup.returncode == 0, "fixture bootout unverified"
    assert _launchctl("print", target).returncode != 0
    assert previous_pid is not None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _pid_exists(previous_pid) is True:
        time.sleep(0.1)
    assert _pid_exists(previous_pid) is False, "old fixture PID remains or is unknown"
