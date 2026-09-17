"""Failure cleanup contract for the dashboard Gemini adapter."""
from __future__ import annotations

import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "hooks" / "spawn_gemini_preregistered.sh"


def test_preregistered_launcher_parses_with_bash() -> None:
    result = subprocess.run(
        ["bash", "-n", str(LAUNCHER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_preregistered_failure_cleanup_retires_identity_before_deleting_token() -> None:
    # Behavior is exercised end to end in test_gemini_new_agent.py (rollback)
    # and test_child_lifecycle_isolation.py (refusals); this pins the order.
    text = LAUNCHER.read_text(encoding="utf-8")
    cleanup = text[text.index("cleanup_failure() {"):text.index("trap cleanup_failure EXIT")]

    release = 'mail_helper release --project-key "$PROJECT_KEY"'
    # The verified common cleanup proves the durable token, retires, and only
    # then removes it; the handoff is spent only after that succeeded.
    retire = '"$CLEANUP_HELPER" "$CHILD_NAME" ) >/dev/null 2>&1; then'
    handoff_delete = 'rm -f "$CHILD_TOKEN_FILE" "$CHILD_TOKEN_FILE.binding.json"'

    assert release in cleanup
    assert retire in cleanup
    assert handoff_delete in cleanup
    assert cleanup.index(release) < cleanup.index(retire) < cleanup.index(handoff_delete)
    assert 'mail_helper retire' not in cleanup
    assert '"$DURABLE_TOKEN" "$GIT_EXCLUDES_FILE"' not in cleanup
