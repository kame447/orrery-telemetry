from __future__ import annotations

from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()


def read(path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (root / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}")
    write(path, text.replace(old, new, 1))


def insert_after(path: str, anchor: str, addition: str) -> None:
    replace_once(path, anchor, anchor + addition)


adapter = "hooks/spawn_gemini_preregistered.sh"
direct = "hooks/spawn_gemini_child.sh"

# Dashboard Gemini adapter: reuse the shared Phase 5c handoff proof.
insert_after(
    adapter,
    'CLEANUP_HELPER="$HOOKS_DIR/cleanup-child-agent.sh"\n',
    'REGISTER_LIB="${AGENTSTACK_REGISTER_LIB:-$AGENTSTACK_HOME_DIR/bin/lib/agentstack-register.sh}"\n'
    '[[ -f "$REGISTER_LIB" ]] || { echo "$PROG: shared registration helper is missing" >&2; exit 1; }\n'
    '# shellcheck disable=SC1090\n. "$REGISTER_LIB"\n',
)

insert_after(
    adapter,
    'RESOURCES="$(validate_resources)" || exit 2\n',
    '\nif ! ags_prepare_preregistered_handoff "$CHILD_NAME" "$WORK_DIR" \\\n'
    '    "$CHILD_TOKEN_FILE" "$PARENT_AGENT" "$PROJECT_KEY"; then\n'
    '  echo "$PROG: pre-registered child handoff does not own this workspace/project" >&2\n'
    '  exit 1\n'
    'fi\n'
    'HANDOFF_CONTEXT_JSON="$AGS_PREREGISTERED_CONTEXT_JSON"\n'
    'HANDOFF_TOKEN="$AGS_PREREGISTERED_REGISTRATION_TOKEN"\n'
    'HANDOFF_OWNER_PREEXISTED="$AGS_PREREGISTERED_OWNER_PREEXISTED"\n'
    'unset AGS_PREREGISTERED_CONTEXT_JSON AGS_PREREGISTERED_REGISTRATION_TOKEN\n'
    'unset AGS_PREREGISTERED_OWNER_PREEXISTED\n'
    'PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"\n',
)

replace_once(
    adapter,
    'DURABLE_TOKEN="$RUNTIME_DIR/agent_token_$TOKEN_KEY"\n',
    'DURABLE_TOKEN="$(ags_registration_token_file "$CHILD_NAME")" || { echo "$PROG: invalid child identity" >&2; exit 1; }\n'
    'OWNER_READY=false\n',
)

# Failure cleanup is authorized by the same strong owner as the launch. A
# refused cleanup preserves state/worktree instead of deleting a newer owner.
text = read(adapter)
start = text.index('cleanup_failure() {\n')
end_marker = 'trap cleanup_failure EXIT\n'
end = text.index(end_marker, start) + len(end_marker)
cleanup = r'''cleanup_failure() {
  status=$?
  if [[ $status -ne 0 ]]; then
    if [[ "$TMUX_STARTED" == true && -n "$CHILD_NAME" ]]; then
      tmux kill-session -t "=$CHILD_NAME" >/dev/null 2>&1 || true
      TMUX_STARTED=false
    fi
    if [[ "$OWNER_READY" == true && -n "$CHILD_NAME" && -x "$CLEANUP_HELPER" ]]; then
      cleanup_dir="$WORK_DIR"
      [[ "$WORKTREE_CREATED" == true && -d "$WORKTREE_DIR" ]] && cleanup_dir="$WORKTREE_DIR"
      if ! (cd "$cleanup_dir" && "$CLEANUP_HELPER" "$CHILD_NAME"); then
        echo "$PROG: validated cleanup refused; preserving child state/worktree for recovery" >&2
        return
      fi
    elif [[ "$OWNER_READY" == true ]]; then
      echo "$PROG: cleanup helper unavailable; preserving child state/worktree for recovery" >&2
      return
    fi
    if [[ "$WORKTREE_CREATED" == true ]]; then
      git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1 || true
      git -C "$SOURCE_REPO" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
    fi
    rm -f "$TASK_EVENT_FILE" "$RUNNER_FILE" "$MCP_CONFIG" "$GIT_EXCLUDES_FILE" "$TASK_FILE"
  fi
}
trap cleanup_failure EXIT
'''
write(adapter, text[:start] + cleanup + text[end:])

replace_once(
    adapter,
    '# Consume the one-shot token into the stable per-agent runtime path expected by\n'
    '# the MCP wrapper. The token value and token-file path are not embedded in the\n'
    '# workspace MCP config.\n'
    '( umask 077 && cat "$CHILD_TOKEN_FILE" > "$DURABLE_TOKEN" )\n'
    'chmod 600 "$DURABLE_TOKEN"\n'
    'rm -f "$CHILD_TOKEN_FILE"\n',
    '# Publish the validated one-shot handoff as the same durable owner format used\n'
    '# by Claude/Codex. Existing strong owners were already validated above.\n'
    'if [[ "$HANDOFF_OWNER_PREEXISTED" != "1" ]]; then\n'
    '  ags_store_registration_token "$CHILD_NAME" "$HANDOFF_TOKEN" \\\n'
    '    "$HANDOFF_CONTEXT_JSON" preregister-child || {\n'
    '      echo "$PROG: could not publish durable child ownership" >&2; exit 1; }\n'
    'fi\n'
    'OWNER_READY=true\n'
    'if [[ "$CHILD_TOKEN_FILE" != "$DURABLE_TOKEN" ]]; then\n'
    '  rm -f "$CHILD_TOKEN_FILE"\n'
    'fi\n',
)

insert_after(
    adapter,
    'WORKTREE_CREATED=true\n',
    '\nif ! ags_apply_owned_workspace "$CHILD_NAME" "$WORKTREE_DIR" \\\n'
    '    "$DURABLE_TOKEN" "$HANDOFF_TOKEN"; then\n'
    '  echo "$PROG: created worktree does not match child ownership" >&2\n'
    '  exit 1\n'
    'fi\n'
    'HANDOFF_CONTEXT_JSON="$AGENTSTACK_PROJECT_CONTEXT_JSON"\n'
    'PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"\n'
    'unset AGS_OWNED_REGISTRATION_TOKEN\n',
)

# Runner receives the validated tuple as metadata; cleanup still revalidates it.
insert_after(
    adapter,
    'export AGENTSTACK_PROJECT_KEY=$(printf \'%q\' "$PROJECT_KEY")\n',
    'export AGENTSTACK_PROJECT_CONTEXT=1\n'
    'export AGENTSTACK_PROJECT_REPOSITORY=$(printf \'%q\' "${AGENTSTACK_PROJECT_REPOSITORY:-}")\n'
    'export AGENTSTACK_PROJECT_WORK_DIR=$(printf \'%q\' "${AGENTSTACK_PROJECT_WORK_DIR:-}")\n'
    'export AGENTSTACK_PROJECT_WORKTREE_ROOT=$(printf \'%q\' "${AGENTSTACK_PROJECT_WORKTREE_ROOT:-}")\n'
    'export AGENTSTACK_PROTECTED_ROOTS=$(printf \'%q\' "${AGENTSTACK_PROTECTED_ROOTS:-}")\n'
    'export AGENTSTACK_PROJECT_CONTEXT_JSON=$(printf \'%q\' "${AGENTSTACK_PROJECT_CONTEXT_JSON:-}")\n',
)

# Keep parent reporting, then hand release/retire/local identity deletion to the
# common validated cleanup. Do not delete the durable token if cleanup refuses.
text = read(adapter)
runner = text.index('cat > "$RUNNER_FILE" <<EOF\n')
report_end_marker = '--runner-status "\\$child_status" || true\n'
report_end = text.index(report_end_marker, runner) + len(report_end_marker)
echo_marker = 'echo "[antigravity] child finished; worktree retained at '
end = text.index(echo_marker, report_end)
runner_cleanup = r'''
cleanup_status=0
if [[ -x $(printf '%q' "$CLEANUP_HELPER") ]]; then
  $(printf '%q' "$CLEANUP_HELPER") $(printf '%q' "$CHILD_NAME") || cleanup_status=\$?
else
  cleanup_status=1
fi
if [[ "\$cleanup_status" -eq 0 ]]; then
  rm -f $(printf '%q' "$TASK_EVENT_FILE") $(printf '%q' "$MCP_CONFIG") \
    $(printf '%q' "$GIT_EXCLUDES_FILE") $(printf '%q' "$RUNNER_FILE")
else
  echo "[antigravity] validated cleanup refused; child ownership state retained for recovery" >&2
fi
'''
write(adapter, text[:report_end] + runner_cleanup + text[end:])

# Direct Gemini path: bind preregistration to the actual source workspace.
replace_once(
    direct,
    '      --model "$MODEL" --task-description "Delegated Antigravity child" \\\n      --token-file-out "$TOKEN_FILE"\n',
    '      --model "$MODEL" --task-description "Delegated Antigravity child" \\\n'
    '      --work-dir "$WORK_DIR" --token-file-out "$TOKEN_FILE"\n',
)

# Failure cleanup uses the shared owner boundary from the actual source/worktree.
text = read(direct)
start = text.index('cleanup_failure() {\n')
end = text.index('trap cleanup_failure EXIT\n', start) + len('trap cleanup_failure EXIT\n')
direct_cleanup = r'''cleanup_failure() {
  status=$?
  if [[ $status -ne 0 ]]; then
    if [[ "$TMUX_STARTED" == true && -n "$CHILD_NAME" ]]; then
      tmux kill-session -t "=$CHILD_NAME" >/dev/null 2>&1 || true
      TMUX_STARTED=false
    fi
    if [[ "$PREREGISTERED" == true && -n "$CHILD_NAME" && -x "$CLEANUP_HELPER" ]]; then
      cleanup_dir="$WORK_DIR"
      [[ "$WORKTREE_CREATED" == true && -d "$WORKTREE_DIR" ]] && cleanup_dir="$WORKTREE_DIR"
      if ! (cd "$cleanup_dir" && "$CLEANUP_HELPER" "$CHILD_NAME"); then
        echo "$PROG: validated cleanup refused; preserving child state/worktree for recovery" >&2
        return
      fi
    elif [[ "$PREREGISTERED" == true ]]; then
      echo "$PROG: cleanup helper unavailable; preserving child state/worktree for recovery" >&2
      return
    fi
    if [[ "$WORKTREE_CREATED" == true && -n "$WORKTREE_DIR" ]]; then
      git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1 || true
      [[ -n "$BRANCH_NAME" ]] && git -C "$SOURCE_REPO" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
    fi
    remove_managed_name
    [[ -n "$MCP_CONFIG" ]] && rm -f "$MCP_CONFIG" 2>/dev/null || true
    [[ -n "$GIT_EXCLUDES_FILE" ]] && rm -f "$GIT_EXCLUDES_FILE" 2>/dev/null || true
    rm -f "$TOKEN_FILE" "$TASK_RAW_FILE" "$TASK_EVENT_FILE" "$RUNNER_FILE"
  fi
}
trap cleanup_failure EXIT
'''
write(direct, text[:start] + direct_cleanup + text[end:])

# Direct runner: report first, then common validated cleanup; remove only
# provider-temporary files after cleanup succeeds.
text = read(direct)
runner = text.index('cat > "$RUNNER_FILE" <<EOF\n')
report_end = text.index(report_end_marker, runner) + len(report_end_marker)
echo_pos = text.index(echo_marker, report_end)
direct_runner_cleanup = r'''
cleanup_status=0
if [[ -x $(printf '%q' "$CLEANUP_HELPER") ]]; then
  $(printf '%q' "$CLEANUP_HELPER") $(printf '%q' "$CHILD_NAME") || cleanup_status=\$?
else
  cleanup_status=1
fi
if [[ "\$cleanup_status" -eq 0 ]]; then
  rm -f $(printf '%q' "$TASK_EVENT_FILE") $(printf '%q' "$TOKEN_FILE") \
    $(printf '%q' "$MCP_CONFIG") $(printf '%q' "$GIT_EXCLUDES_FILE") $(printf '%q' "$RUNNER_FILE")
else
  echo "[antigravity] validated cleanup refused; child ownership state retained for recovery" >&2
fi
'''
write(direct, text[:report_end] + direct_runner_cleanup + text[echo_pos:])

# Focused contract tests: the shared helper is already behavior-tested in 5c;
# these assert the two Gemini adapters wire to it without reimplementing it.
cleanup_test = root / "tests/test_gemini_preregistered_cleanup.py"
cleanup_test.write_text('''"""Ownership-safe cleanup contract for the Dashboard Gemini adapter."""\nfrom __future__ import annotations\n\nimport pathlib\nimport subprocess\n\nROOT = pathlib.Path(__file__).resolve().parent.parent\nLAUNCHER = ROOT / "hooks" / "spawn_gemini_preregistered.sh"\n\n\ndef test_preregistered_launcher_parses_with_bash() -> None:\n    result = subprocess.run(["bash", "-n", str(LAUNCHER)], cwd=ROOT, text=True, capture_output=True, check=False)\n    assert result.returncode == 0, result.stderr\n\n\ndef test_handoff_is_validated_before_token_consumption_or_worktree_creation() -> None:\n    text = LAUNCHER.read_text(encoding="utf-8")\n    proof = text.index("ags_prepare_preregistered_handoff")\n    consume = text.index('rm -f "$CHILD_TOKEN_FILE"')\n    worktree = text.index('git -C "$SOURCE_REPO" worktree add')\n    assert proof < consume < worktree\n    assert 'ags_store_registration_token "$CHILD_NAME" "$HANDOFF_TOKEN"' in text\n    assert 'ags_apply_owned_workspace "$CHILD_NAME" "$WORKTREE_DIR"' in text\n\n\ndef test_failure_cleanup_delegates_identity_mutation_to_common_cleanup() -> None:\n    text = LAUNCHER.read_text(encoding="utf-8")\n    cleanup = text[text.index("cleanup_failure() {"):text.index("trap cleanup_failure EXIT")]\n    assert '$CLEANUP_HELPER" "$CHILD_NAME"' in cleanup\n    assert 'mail_helper release' not in cleanup\n    assert 'mail_helper retire' not in cleanup\n    assert 'rm -f "$TASK_EVENT_FILE"' in cleanup\n    assert '"$DURABLE_TOKEN"' not in cleanup.split("rm -f", 1)[-1]\n\n\ndef test_runner_reports_then_uses_common_cleanup_without_manual_release_or_retire() -> None:\n    text = LAUNCHER.read_text(encoding="utf-8")\n    runner = text[text.index('cat > "$RUNNER_FILE" <<EOF'):text.index('chmod 700 "$RUNNER_FILE"')]\n    report = runner.index('"$MAIL_HELPER") report --project-key')\n    cleanup = runner.index('"$CLEANUP_HELPER")')\n    assert report < cleanup\n    assert '"$MAIL_HELPER") release --project-key' not in runner\n    assert '"$MAIL_HELPER") retire --project-key' not in runner\n''', encoding="utf-8")

launch_path = root / "tests/test_gemini_launch.py"
launch = launch_path.read_text(encoding="utf-8")
old = '''    assert '"$PREREGISTER" --project-key "$PROJECT_KEY" --program antigravity' in text\n'''
new = old + '''    assert '--work-dir "$WORK_DIR" --token-file-out "$TOKEN_FILE"' in text\n'''
if launch.count(old) != 1:
    raise SystemExit("gemini launch prereg anchor missing")
launch = launch.replace(old, new, 1)
old_lifecycle = '''    assert 'mail_helper reserve --project-key "$PROJECT_KEY"' in text\n    assert '$(printf \'%q\' "$MAIL_HELPER") report --project-key' in text\n    assert '$(printf \'%q\' "$MAIL_HELPER") release --project-key' in text\n    assert '$(printf \'%q\' "$MAIL_HELPER") retire --project-key' in text\n    assert 'rm -f $(printf \'%q\' "$TASK_EVENT_FILE") $(printf \'%q\' "$TOKEN_FILE")' in text\n'''
new_lifecycle = '''    assert 'mail_helper reserve --project-key "$PROJECT_KEY"' in text\n    assert '$(printf \'%q\' "$MAIL_HELPER") report --project-key' in text\n    runner = text[text.index('cat > "$RUNNER_FILE" <<EOF'):text.index('chmod 700 "$RUNNER_FILE"')]\n    assert '$(printf \'%q\' "$CLEANUP_HELPER") $(printf \'%q\' "$CHILD_NAME")' in runner\n    assert '$(printf \'%q\' "$MAIL_HELPER") release --project-key' not in runner\n    assert '$(printf \'%q\' "$MAIL_HELPER") retire --project-key' not in runner\n'''
if launch.count(old_lifecycle) != 1:
    raise SystemExit("gemini lifecycle anchor missing")
launch_path.write_text(launch.replace(old_lifecycle, new_lifecycle, 1), encoding="utf-8")

wiring = root / "tests/test_phase5_gemini_child_ownership.py"
wiring.write_text('''from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\nADAPTER = ROOT / "hooks" / "spawn_gemini_preregistered.sh"\nDIRECT = ROOT / "hooks" / "spawn_gemini_child.sh"\n\n\ndef test_dashboard_adapter_reuses_shared_owner_contract():\n    text = ADAPTER.read_text(encoding="utf-8")\n    assert '. "$REGISTER_LIB"' in text\n    assert 'ags_prepare_preregistered_handoff "$CHILD_NAME" "$WORK_DIR"' in text\n    assert 'ags_store_registration_token "$CHILD_NAME" "$HANDOFF_TOKEN"' in text\n    assert 'ags_apply_owned_workspace "$CHILD_NAME" "$WORKTREE_DIR"' in text\n    assert '( umask 077 && cat "$CHILD_TOKEN_FILE" > "$DURABLE_TOKEN" )' not in text\n\n\ndef test_direct_gemini_preregistration_binds_the_actual_source_workspace():\n    text = DIRECT.read_text(encoding="utf-8")\n    prereg = text[text.index('CHILD_NAME="$('):text.index('PREREGISTERED=true') ]\n    assert '--work-dir "$WORK_DIR"' in prereg\n\n\ndef test_both_gemini_failure_paths_use_common_validated_cleanup():\n    for path in (ADAPTER, DIRECT):\n        text = path.read_text(encoding="utf-8")\n        cleanup = text[text.index('cleanup_failure() {'):text.index('trap cleanup_failure EXIT')]\n        assert '$CLEANUP_HELPER" "$CHILD_NAME"' in cleanup\n        assert 'mail_helper release' not in cleanup\n        assert 'mail_helper retire' not in cleanup\n        assert 'preserving child state/worktree for recovery' in cleanup\n''', encoding="utf-8")

# Explain only the changed responsibility; no new Gemini orchestration concept.
for doc in ("docs/antigravity.md", "docs/antigravity.en.md"):
    path = root / doc
    text = path.read_text(encoding="utf-8")
    if "validated child ownership" not in text.lower():
        if doc.endswith(".en.md"):
            note = "\n\n### Validated child ownership\n\nDelegated Gemini launchers use the same durable child ownership boundary as the core launchers. Preregistration is bound to the intended source workspace before a worktree is created; the Dashboard adapter validates its one-shot handoff before consuming the token. Reservation release, retirement, and identity deletion are delegated to the shared validated cleanup path. If ownership changes, cleanup fails closed and retains recovery state instead of deleting the replacement.\n"
        else:
            note = "\n\n### Validated child ownership\n\n委譲Gemini launcherもcore launcherと同じdurable child ownership境界を使います。preregistrationはworktree作成前に対象source workspaceへ結び付け、Dashboard adapterはone-shot handoffをtoken消費前に検証します。reservation release・retire・identity削除は共通のvalidated cleanupへ委譲し、ownershipが変化していた場合はreplacementを削除せず回復用stateを残します。\n"
        path.write_text(text + note, encoding="utf-8")

print("prepared phase5d")
