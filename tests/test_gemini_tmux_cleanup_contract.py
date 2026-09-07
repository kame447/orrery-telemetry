from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS = (
    ROOT / "hooks" / "spawn_gemini_child.sh",
    ROOT / "hooks" / "spawn_gemini_preregistered.sh",
)


def test_failed_gemini_launch_kills_tmux_before_removing_worktree():
    for hook in HOOKS:
        text = hook.read_text(encoding="utf-8")
        assert "TMUX_STARTED=false" in text, hook
        assert "TMUX_STARTED=true" in text, hook
        assert 'tmux kill-session -t "=$CHILD_NAME"' in text, hook
        cleanup = text.split("cleanup_failure() {", 1)[1].split("}\ntrap cleanup_failure EXIT", 1)[0]
        assert cleanup.index('tmux kill-session -t "=$CHILD_NAME"') < cleanup.index('git -C "$SOURCE_REPO" worktree remove --force'), hook
