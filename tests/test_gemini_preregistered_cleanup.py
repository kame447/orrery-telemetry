"""Ownership-safe cleanup contract for the Dashboard Gemini adapter."""
from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "hooks" / "spawn_gemini_preregistered.sh"


def test_preregistered_launcher_parses_with_bash() -> None:
    result = subprocess.run(["bash", "-n", str(LAUNCHER)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_handoff_is_validated_before_token_consumption_or_worktree_creation() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    proof = text.index("ags_prepare_preregistered_handoff")
    consume = text.index('rm -f "$CHILD_TOKEN_FILE"')
    worktree = text.index('git -C "$SOURCE_REPO" worktree add')
    assert proof < consume < worktree
    assert 'ags_store_registration_token "$CHILD_NAME" "$HANDOFF_TOKEN"' in text
    assert 'ags_apply_owned_workspace "$CHILD_NAME" "$WORKTREE_DIR"' in text


def test_failure_cleanup_delegates_identity_mutation_to_common_cleanup() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    cleanup = text[text.index("cleanup_failure() {"):text.index("trap cleanup_failure EXIT")]
    assert '$CLEANUP_HELPER" "$CHILD_NAME"' in cleanup
    assert 'mail_helper release' not in cleanup
    assert 'mail_helper retire' not in cleanup
    assert 'rm -f "$TASK_EVENT_FILE"' in cleanup
    assert '"$DURABLE_TOKEN"' not in cleanup.split("rm -f", 1)[-1]


def test_runner_reports_then_uses_common_cleanup_without_manual_release_or_retire() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    runner = text[text.index('cat > "$RUNNER_FILE" <<EOF'):text.index('chmod 700 "$RUNNER_FILE"')]
    report = runner.index('"$MAIL_HELPER") report --project-key')
    cleanup = runner.index('"$CLEANUP_HELPER")')
    assert report < cleanup
    assert '"$MAIL_HELPER") release --project-key' not in runner
    assert '"$MAIL_HELPER") retire --project-key' not in runner
