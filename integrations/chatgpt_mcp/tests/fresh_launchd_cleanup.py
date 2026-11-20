"""Best-effort teardown and complete observations for isolated launchd tests."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
import socket
import subprocess
import time


def cleanup_launchagent(
    *, target: str, attempted: bool, observed_pids: Iterable[int],
    launchctl: Callable[..., subprocess.CompletedProcess[str]],
    pid_exists: Callable[[int], bool | None], port: int | None = None,
    log_path: Path | None = None, forbidden: tuple[str, ...] = (),
    body_error: BaseException | None = None,
) -> None:
    failures: list[str] = []

    def check(code: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception:
            failures.append(code)

    def bootout() -> None:
        assert launchctl("bootout", target).returncode == 0

    def job_gone() -> None:
        assert launchctl("print", target).returncode != 0

    def pid_gone(pid: int) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and pid_exists(pid) is True:
            time.sleep(0.1)
        assert pid_exists(pid) is False

    def port_closed() -> None:
        with socket.socket() as client:
            client.settimeout(0.5)
            assert client.connect_ex(("127.0.0.1", port)) != 0

    def log_clean() -> None:
        output = log_path.read_text(errors="replace")
        assert all(value not in output for value in forbidden)

    if attempted:
        check("BOOTOUT_UNVERIFIED", bootout)
    check("JOB_REMOVAL_UNVERIFIED", job_gone)
    for pid in sorted(set(observed_pids)):
        check("PID_EXIT_UNVERIFIED", lambda pid=pid: pid_gone(pid))
    if port is not None:
        check("PORT_CLOSE_UNVERIFIED", port_closed)
    if log_path is not None:
        check("LOG_PRIVACY_UNVERIFIED", log_clean)

    if failures:
        summary = "isolated LaunchAgent cleanup: " + ", ".join(dict.fromkeys(failures))
        if body_error is not None:
            body_error.add_note(summary)
        else:
            raise AssertionError(summary) from None
