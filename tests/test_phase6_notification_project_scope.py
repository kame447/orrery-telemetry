from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

from agentstack_mail.storage import emit_notification_signal

ROOT = pathlib.Path(__file__).resolve().parents[1]
WATCHER = ROOT / "hooks" / "watch_agent_mail_signals.sh"
REGISTER_LIB = ROOT / "bin" / "lib" / "agentstack-register.sh"
PROJECT_CONTEXT = ROOT / "hooks" / "project-context.sh"


def _git_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def _store_owner(runtime: pathlib.Path, repo: pathlib.Path, token: str) -> None:
    env = dict(os.environ)
    env["AGENTSTACK_RUNTIME_DIR"] = str(runtime)
    env["AGENTSTACK_PYTHON"] = sys.executable
    script = f"""set -euo pipefail
source {shlex.quote(str(PROJECT_CONTEXT))}
source {shlex.quote(str(REGISTER_LIB))}
context="$(agentstack_resolve_invocation_context {shlex.quote(str(repo))})"
ags_store_registration_token SharedCurie {shlex.quote(token)} "$context" phase6-test
"""
    completed = subprocess.run(
        ["/bin/bash", "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _fake_tmux(fake_bin: pathlib.Path) -> pathlib.Path:
    fake_bin.mkdir(parents=True)
    tmux = fake_bin / "tmux"
    tmux.write_text(
        r"""#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_TMUX_LOG"
case "$1" in
  has-session) exit 0 ;;
  display-message) printf '%s\n' "$FAKE_TMUX_CWD"; exit 0 ;;
  capture-pane) printf 'Claude ❯\n'; exit 0 ;;
  send-keys) exit 0 ;;
  *) exit 1 ;;
esac
""",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    return tmux


def _run_case(tmp_path: pathlib.Path, *, signal_repo: pathlib.Path, owner_repo: pathlib.Path, expect_delivery: bool) -> tuple[dict, str, pathlib.Path]:
    runtime = tmp_path / "runtime"
    signals = tmp_path / "signals"
    fake_bin = tmp_path / "fake-bin"
    tmux_log = tmp_path / "tmux.log"
    token = "phase6-owner-token"
    _store_owner(runtime, owner_repo, token)
    _fake_tmux(fake_bin)

    signal_file = signals / "projects" / "project-a" / "agents" / "SharedCurie" / "73.signal"
    signal_file.parent.mkdir(parents=True)
    signal_file.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-15T00:00:00+00:00",
                "project": "project-a",
                "project_key": str(signal_repo.resolve()),
                "agent": "SharedCurie",
                "message": {
                    "id": 73,
                    "from": "ParentCurie",
                    "subject": "project scoped notification",
                    "importance": "high",
                },
            }
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '/usr/bin:/bin')}",
            "AGENTSTACK_SIGNALS_DIR": str(signals),
            "AGENTSTACK_RUNTIME_DIR": str(runtime),
            "AGENTSTACK_MAIL_WATCHER_LOCK_DIR": str(tmp_path / "watcher.lock"),
            "AGENTSTACK_PYTHON": sys.executable,
            "AGENTSTACK_REGISTER_LIB": str(REGISTER_LIB),
            "AGENTSTACK_MCP_URL": "http://127.0.0.1:9/mcp",
            "FAKE_TMUX_CWD": str(owner_repo),
            "FAKE_TMUX_LOG": str(tmux_log),
            "TMUX_TIMEOUT": "2",
        }
    )
    watcher = subprocess.Popen(
        ["/bin/bash", str(WATCHER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    state_file = runtime / "notify-state.json"
    state: dict = {}
    deadline = time.time() + 12
    try:
        while time.time() < deadline:
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    state = {}
            results = [
                entry.get("last_result")
                for entry in state.values()
                if isinstance(entry, dict)
            ]
            if expect_delivery and "success" in results and not signal_file.exists():
                break
            if not expect_delivery and "project_mismatch" in results:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(f"watcher did not reach expected state: {state}")
    finally:
        watcher.terminate()
        out, err = watcher.communicate(timeout=10)
        if watcher.returncode not in (0, -15):
            raise AssertionError(out + err)

    calls = tmux_log.read_text(encoding="utf-8")
    return state, calls, signal_file


def test_signal_payload_carries_canonical_project_key(tmp_path: pathlib.Path) -> None:
    settings = SimpleNamespace(
        notifications=SimpleNamespace(
            enabled=True,
            debounce_ms=0,
            signals_dir=str(tmp_path / "signals"),
            include_metadata=True,
        )
    )
    emitted = asyncio.run(
        emit_notification_signal(
            settings,
            "project-a-slug",
            "SharedCurie",
            {"id": 11, "from": "ParentCurie", "subject": "ready", "importance": "high"},
            project_key="canonical-project-a",
        )
    )
    assert emitted is True
    signal = json.loads(
        (tmp_path / "signals" / "projects" / "project-a-slug" / "agents" / "SharedCurie" / "11.signal").read_text(encoding="utf-8")
    )
    assert signal["project"] == "project-a-slug"
    assert signal["project_key"] == "canonical-project-a"


def test_watcher_injects_only_into_the_strongly_owned_project(tmp_path: pathlib.Path) -> None:
    project_a = _git_repo(tmp_path / "project-a")
    state, calls, signal = _run_case(
        tmp_path / "matching",
        signal_repo=project_a,
        owner_repo=project_a,
        expect_delivery=True,
    )
    assert any(entry.get("last_result") == "success" for entry in state.values())
    assert not signal.exists()
    assert "has-session -t =SharedCurie" in calls
    assert "display-message -t =SharedCurie" in calls
    assert "capture-pane -t =SharedCurie" in calls
    assert "send-keys -t =SharedCurie" in calls


def test_stale_other_project_signal_fails_closed_before_capture_or_injection(tmp_path: pathlib.Path) -> None:
    project_a = _git_repo(tmp_path / "project-a")
    project_b = _git_repo(tmp_path / "project-b")
    state, calls, signal = _run_case(
        tmp_path / "mismatch",
        signal_repo=project_a,
        owner_repo=project_b,
        expect_delivery=False,
    )
    mismatch = [entry for entry in state.values() if entry.get("last_result") == "project_mismatch"]
    assert mismatch and mismatch[0]["project_key"] == str(project_a.resolve())
    assert signal.exists(), "mismatched signal must remain pending"
    assert "display-message -t =SharedCurie" in calls
    assert "capture-pane" not in calls
    assert "send-keys" not in calls


def test_delivery_state_is_project_scoped_in_source() -> None:
    source = WATCHER.read_text(encoding="utf-8")
    assert 'compound = f"{project}:{agent}:{msg_key}"' in source
    assert 'acquire_delivery_lease "$signal_project_key" "$agent_name" "$msg_key"' in source
    assert 'deliver_worker "$signal_file" "$signal_project_key" "$agent_name" "$msg_key"' in source
