from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPAWN = ROOT / "hooks" / "spawn_child.sh"


def _direct() -> str:
    text = SPAWN.read_text(encoding="utf-8")
    return text[text.index("# --- Legacy transport bearer"):]


def test_direct_launcher_parses_with_bash():
    result = subprocess.run(["bash", "-n", str(SPAWN)], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_parent_workspace_is_proven_before_mail_health_or_registration():
    whole = SPAWN.read_text(encoding="utf-8")
    parent = whole.index('ags_apply_owned_workspace "$PARENT_NAME" "$WORK_DIR"')
    health = whole.index('# --- 1. サーバー稼働確認 ---')
    register = whole.index('REGISTER_RESULT=$(call_mcp "register_agent"', health)
    assert parent < health < register


def test_direct_registration_uses_local_claim_and_strong_owner_publication():
    text = _direct()
    begin = text.index('ags_begin_registration_ownership "$DIRECT_PARENT_CONTEXT_JSON"')
    register = text.index('REGISTER_RESULT=$(call_mcp "register_agent"')
    commit = text.index('ags_commit_registration_ownership "$DIRECT_PARENT_CONTEXT_JSON"')
    state = text.index('adopt_registered_token_response', commit)
    assert begin < register < commit < state
    assert 'ags_local_agent_name_conflicts "$PROJECT_KEY" "$candidate" candidate' in text


def test_direct_failure_cleanup_is_common_validated_cleanup_only():
    text = _direct()
    cleanup = text[text.index('cleanup_on_failure() {'):text.index('trap cleanup_on_failure EXIT')]
    assert 'cleanup-child-agent.sh" "$CHILD_NAME"' in cleanup
    assert 'release_file_reservations' not in cleanup
    assert 'retire_agent_with_token_file' not in cleanup
    conflict = text[text.index('if [[ "$HAS_CONFLICT" == "yes" ]]'):text.index('# --- 2c.')]
    assert 'release_file_reservations' not in conflict
    assert 'retire_agent' not in conflict


def test_direct_worktree_refreshes_child_owned_context_before_tmux():
    text = _direct()
    worktree = text.index('WORK_DIR="$WORKTREE_DIR"')
    refresh = text.index('ags_apply_owned_workspace "$CHILD_NAME" "$WORK_DIR"', worktree)
    tmux = text.index('TMUX_ENV_ARGS=', refresh)
    assert worktree < refresh < tmux
    tmux_line = text[tmux:text.index('\n', tmux)]
    for name in (
        'AGENTSTACK_PROJECT_CONTEXT=1',
        'AGENTSTACK_PROJECT_REPOSITORY=',
        'AGENTSTACK_PROJECT_WORK_DIR=',
        'AGENTSTACK_PROJECT_WORKTREE_ROOT=',
        'AGENTSTACK_PROTECTED_ROOTS=',
        'AGENTSTACK_PROJECT_CONTEXT_JSON=',
    ):
        assert name in tmux_line
