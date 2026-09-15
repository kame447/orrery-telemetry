from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "hooks" / "spawn_gemini_preregistered.sh"
DIRECT = ROOT / "hooks" / "spawn_gemini_child.sh"


def test_dashboard_adapter_reuses_shared_owner_contract():
    text = ADAPTER.read_text(encoding="utf-8")
    assert '. "$REGISTER_LIB"' in text
    assert 'ags_prepare_preregistered_handoff "$CHILD_NAME" "$WORK_DIR"' in text
    assert 'ags_store_registration_token "$CHILD_NAME" "$HANDOFF_TOKEN"' in text
    assert 'ags_apply_owned_workspace "$CHILD_NAME" "$WORKTREE_DIR"' in text
    assert '( umask 077 && cat "$CHILD_TOKEN_FILE" > "$DURABLE_TOKEN" )' not in text


def test_direct_gemini_preregistration_binds_the_actual_source_workspace():
    text = DIRECT.read_text(encoding="utf-8")
    prereg = text[text.index('CHILD_NAME="$('):text.index('PREREGISTERED=true') ]
    assert '--work-dir "$WORK_DIR"' in prereg


def test_both_gemini_failure_paths_use_common_validated_cleanup():
    for path in (ADAPTER, DIRECT):
        text = path.read_text(encoding="utf-8")
        cleanup = text[text.index('cleanup_failure() {'):text.index('trap cleanup_failure EXIT')]
        assert '$CLEANUP_HELPER" "$CHILD_NAME"' in cleanup
        assert 'mail_helper release' not in cleanup
        assert 'mail_helper retire' not in cleanup
        assert 'preserving child state/worktree for recovery' in cleanup
