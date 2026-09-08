from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "hooks" / "spawn_gemini_child.sh"
PREREGISTER = ROOT / "bin" / "agentstack-preregister-child"


def test_direct_preregistration_persists_session_bound_token() -> None:
    text = PREREGISTER.read_text(encoding="utf-8")
    assert 'ags_store_registration_token "$registered" "$effective_token"' in text


def test_direct_launch_failure_runs_core_cleanup_for_durable_identity() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    cleanup = text.split("cleanup_failure() {", 1)[1].split(
        "}\ntrap cleanup_failure EXIT", 1
    )[0]

    retire = 'mail_helper retire --project-key "$PROJECT_KEY"'
    durable_cleanup = '"$CLEANUP_HELPER" "$CHILD_NAME"'
    worktree_remove = 'git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_DIR"'

    assert retire in cleanup
    assert durable_cleanup in cleanup
    assert worktree_remove in cleanup
    assert cleanup.index(retire) < cleanup.index(durable_cleanup) < cleanup.index(worktree_remove)
    assert 'AGENTSTACK_PROJECT_KEY="$PROJECT_KEY"' in cleanup
    assert 'AGENTSTACK_RUNTIME_DIR="$RUNTIME_DIR"' in cleanup
