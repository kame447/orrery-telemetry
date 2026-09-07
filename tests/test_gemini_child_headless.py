#!/usr/bin/env python3
"""Regression coverage for delegated Antigravity headless execution."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHILD = ROOT / "hooks" / "spawn_gemini_child.sh"
PREREGISTERED = ROOT / "hooks" / "spawn_gemini_preregistered.sh"
STREAM = ROOT / "bin" / "agentstack-gemini-stream"
CHILD_MAIL = ROOT / "bin" / "agentstack-gemini-child-mail"


def _run_stream(events: list[dict]) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        result_log = pathlib.Path(tmp) / "result.ndjson"
        payload = "".join(json.dumps(event) + "\n" for event in events)
        return subprocess.run(
            [sys.executable, str(STREAM), str(result_log)],
            cwd=ROOT,
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


def test_stream_accepts_success_with_textual_response() -> None:
    result = _run_stream(
        [
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "Task completed without changes.",
                    "denied_actions": [],
                },
            }
        ]
    )
    assert result.returncode == 0, result.stderr
    assert "status=SUCCESS" in result.stdout


def test_stream_rejects_success_without_textual_response() -> None:
    result = _run_stream(
        [
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": "",
                    "denied_actions": [
                        {"action": "read_file", "display_name": "ListDir"}
                    ],
                },
            }
        ]
    )
    assert result.returncode != 0
    assert "no textual response" in result.stderr
    assert "read_file" in result.stderr


def test_stream_rejects_non_success_result() -> None:
    result = _run_stream(
        [
            {
                "event": "result",
                "result": {"status": "ERROR", "response": "failed"},
            }
        ]
    )
    assert result.returncode != 0
    assert "status=ERROR" in result.stderr


def test_child_prompts_anchor_resources_to_the_worktree() -> None:
    for path in (CHILD, PREREGISTERED):
        text = path.read_text(encoding="utf-8")
        assert '"$WORKTREE_DIR" "$SOURCE_REPO"' in text
        assert "Declared resources (worktree-anchored):" in text
        assert "Do not search the source checkout, $HOME, or parent directories" in text
        assert "If a declared resource is absent from this worktree" in text


def test_child_runners_propagate_stream_validation_failure() -> None:
    for path in (CHILD, PREREGISTERED):
        text = path.read_text(encoding="utf-8")
        assert 'pipeline_status=("\\${PIPESTATUS[@]}")' in text
        assert 'stream_status="\\${pipeline_status[2]:-1}"' in text
        assert 'child_status="\\$agy_status"' in text
        assert 'child_status="\\$stream_status"' in text
        assert 'exit "\\$child_status"' in text


def test_child_mail_marks_empty_success_as_incomplete() -> None:
    text = CHILD_MAIL.read_text(encoding="utf-8")
    assert 'if status == "SUCCESS" and not response:' in text
    assert 'status = "INCOMPLETE"' in text
    assert "headless run produced no textual response" in text


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
