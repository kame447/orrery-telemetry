from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOKS = (
    ROOT / "hooks" / "spawn_gemini_child.sh",
    ROOT / "hooks" / "spawn_gemini_preregistered.sh",
)


def _runner_body(text: str) -> str:
    return text.split('cat > "$RUNNER_FILE" <<EOF', 1)[1].split(
        'EOF\nchmod 700 "$RUNNER_FILE"', 1
    )[0]


def test_failed_gemini_launch_kills_tmux_before_removing_worktree():
    for hook in HOOKS:
        text = hook.read_text(encoding="utf-8")
        assert "TMUX_STARTED=false" in text, hook
        assert "TMUX_STARTED=true" in text, hook
        assert 'tmux kill-session -t "=$CHILD_NAME"' in text, hook
        cleanup = text.split("cleanup_failure() {", 1)[1].split("}\ntrap cleanup_failure EXIT", 1)[0]
        assert cleanup.index('tmux kill-session -t "=$CHILD_NAME"') < cleanup.index('git -C "$SOURCE_REPO" worktree remove --force'), hook


def test_successful_gemini_child_cleanup_finishes_before_tmux_session_exits():
    for hook in HOOKS:
        text = hook.read_text(encoding="utf-8")
        runner = _runner_body(text)

        # A delegated child owns its lifecycle: report first, then release and
        # retire, remove transient credentials/config, and finally let the
        # runner exit. Because tmux runs only this runner command, its session
        # naturally disappears afterwards (gone/retired) instead of leaving a
        # shell husk behind as a top-level interactive launcher does.
        report = runner.index(" report --project-key ")
        release = runner.index(" release --project-key ")
        retire = runner.index(" retire --project-key ")
        cleanup = runner.index("$CLEANUP_HELPER")
        final_exit = runner.rindex('exit "\\$child_status"')
        assert report < release < retire < cleanup < final_exit, hook
        assert 'exec "\\$SHELL"' not in runner, hook

        tmux_launch = text.split('tmux new-session -d -s "$CHILD_NAME"', 1)[1]
        assert '"/bin/bash $(printf \'%q\' "$RUNNER_FILE")"' in tmux_launch, hook
