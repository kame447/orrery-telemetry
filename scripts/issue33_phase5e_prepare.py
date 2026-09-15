from __future__ import annotations

from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
path = root / "hooks/spawn_child.sh"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if text.count(old) != 1:
        raise SystemExit(f"anchor count={text.count(old)} for {old[:80]!r}")
    text = text.replace(old, new, 1)

# Direct child launch must inherit a validated parent workspace, not an ambient
# project key. This runs before health/name/registration side effects.
anchor = '''if [[ "$PARENT_NAME" == "unknown" || -z "$PARENT_NAME" ]]; then
    echo "Error: parent agent name is unknown. Set PARENT_AGENT or run inside a tmux session" >&2
    exit 1
fi

# --- Legacy transport bearer (native ORRERY Mail deliberately has none) ---
'''
replacement = '''if [[ "$PARENT_NAME" == "unknown" || -z "$PARENT_NAME" ]]; then
    echo "Error: parent agent name is unknown. Set PARENT_AGENT or run inside a tmux session" >&2
    exit 1
fi

if ! declare -F ags_apply_owned_workspace >/dev/null 2>&1; then
    echo "Error: shared registration ownership helper is unavailable" >&2
    exit 1
fi
if ! ags_apply_owned_workspace "$PARENT_NAME" "$WORK_DIR"; then
    echo "Error: parent '$PARENT_NAME' does not own child workspace: $WORK_DIR" >&2
    exit 1
fi
PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"
DIRECT_PARENT_CONTEXT_JSON="$AGENTSTACK_PROJECT_CONTEXT_JSON"
unset AGS_OWNED_REGISTRATION_TOKEN

# --- Legacy transport bearer (native ORRERY Mail deliberately has none) ---
'''
replace_once(anchor, replacement)

# A server-free name is still unsafe when another local project/session owns
# the host-global spelling.
start = text.index("pick_available_child_agent_name() {")
end = text.index("\n}\n\nretire_agent_with_token_file()", start) + 3
chunk = text[start:end]
old = "            available) printf '%s\\n' \"$candidate\"; return 0 ;;"
new = '''            available)
                if declare -F ags_local_agent_name_conflicts >/dev/null 2>&1 && \
                   ags_local_agent_name_conflicts "$PROJECT_KEY" "$candidate" candidate; then
                    unknowns=0
                    continue
                fi
                printf '%s\\n' "$candidate"; return 0 ;;'''
if chunk.count(old) != 2:
    raise SystemExit(f"available-arm count={chunk.count(old)}")
chunk = chunk.replace(old, new)
text = text[:start] + chunk + text[end:]

# Claim local ownership before Mail registration, then publish the strong owner
# using the server-returned token/name. No new ownership format is introduced.
anchor = '''DIRECT_ONE_SHOT_TOKEN_FILE="$TOKEN_HANDOFF_DIR/direct.$$.${TOKEN_NONCE}.token"
generate_child_token_file "$DIRECT_ONE_SHOT_TOKEN_FILE"
REGISTER_ARGS=$(python3 -c '
'''
replacement = '''DIRECT_ONE_SHOT_TOKEN_FILE="$TOKEN_HANDOFF_DIR/direct.$$.${TOKEN_NONCE}.token"
generate_child_token_file "$DIRECT_ONE_SHOT_TOKEN_FILE"
DIRECT_REGISTRATION_TOKEN="$(ags_read_private_registration_token "$DIRECT_ONE_SHOT_TOKEN_FILE")" || {
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: generated child owner token is unreadable" >&2
    exit 1
}
if ! ags_begin_registration_ownership "$DIRECT_PARENT_CONTEXT_JSON" \
    "$CHILD_NAME_CANDIDATE" "$DIRECT_REGISTRATION_TOKEN" direct-child candidate; then
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: child identity '$CHILD_NAME_CANDIDATE' was claimed concurrently" >&2
    exit 1
fi
REGISTER_ARGS=$(python3 -c '
'''
replace_once(anchor, replacement)

for old_error, new_error in [
    ('''if ! REGISTER_RESULT=$(call_mcp "register_agent" "$REGISTER_ARGS"); then
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: register_agent request failed" >&2
    exit 1
fi
''', '''if ! REGISTER_RESULT=$(call_mcp "register_agent" "$REGISTER_ARGS"); then
    ags_release_registration_ownership
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: register_agent request failed" >&2
    exit 1
fi
'''),
    ('''if printf '%s' "$REGISTER_RESULT" | mcp_response_has_error; then
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: register_agent returned an error" >&2
    exit 1
fi
''', '''if printf '%s' "$REGISTER_RESULT" | mcp_response_has_error; then
    ags_release_registration_ownership
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: register_agent returned an error" >&2
    exit 1
fi
'''),
    ('''if [[ -z "$CHILD_NAME" || ! "$CHILD_NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "Error: register_agent returned no valid child agent name" >&2
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    exit 1
fi
''', '''if [[ -z "$CHILD_NAME" || ! "$CHILD_NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "Error: register_agent returned no valid child agent name" >&2
    ags_release_registration_ownership
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    exit 1
fi
'''),
]:
    replace_once(old_error, new_error)

anchor = '''if [[ "$CHILD_NAME" != "$CHILD_NAME_CANDIDATE" ]]; then
    echo "[spawn_child] register_agent normalized '$CHILD_NAME_CANDIDATE' to actual identity '$CHILD_NAME'" >&2
fi

# Adopt the token the server persisted, not the one we sent. Legacy servers
'''
replacement = '''if [[ "$CHILD_NAME" != "$CHILD_NAME_CANDIDATE" ]]; then
    echo "[spawn_child] register_agent normalized '$CHILD_NAME_CANDIDATE' to actual identity '$CHILD_NAME'" >&2
    if ags_local_agent_name_conflicts "$PROJECT_KEY" "$CHILD_NAME" substitution; then
        ags_release_registration_ownership
        rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
        echo "Error: server-returned child identity '$CHILD_NAME' conflicts with local ownership" >&2
        exit 1
    fi
fi
DIRECT_EFFECTIVE_TOKEN="$(printf '%s' "$REGISTER_RESULT" | ags_extract_registration_token)"
[[ -n "$DIRECT_EFFECTIVE_TOKEN" ]] || DIRECT_EFFECTIVE_TOKEN="$DIRECT_REGISTRATION_TOKEN"
if ! ags_commit_registration_ownership "$DIRECT_PARENT_CONTEXT_JSON" \
    "$CHILD_NAME_CANDIDATE" "$CHILD_NAME" "$DIRECT_EFFECTIVE_TOKEN" direct-child; then
    ags_release_registration_ownership
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: could not publish verified ownership for '$CHILD_NAME'" >&2
    exit 1
fi

# Adopt the token the server persisted, not the one we sent. Legacy servers
'''
replace_once(anchor, replacement)

# If legacy state persistence fails after strong-owner publication, run the
# same validated cleanup path rather than deleting name-keyed files directly.
old = '''if ! CHILD_TOKEN_FILE="$(
    printf '%s' "$REGISTER_RESULT" |
        adopt_registered_token_response "$CHILD_NAME" "$PROJECT_KEY" \
            "$DIRECT_ONE_SHOT_TOKEN_FILE"
)"; then
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    echo "Error: failed to persist the registered child token" >&2
    exit 1
fi
'''
new = '''if ! CHILD_TOKEN_FILE="$(
    printf '%s' "$REGISTER_RESULT" |
        adopt_registered_token_response "$CHILD_NAME" "$PROJECT_KEY" \
            "$DIRECT_ONE_SHOT_TOKEN_FILE"
)"; then
    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"
    (cd "$WORK_DIR" && CHILD_REGISTRATION_TOKEN="$DIRECT_EFFECTIVE_TOKEN" \
        /bin/bash "$HOOKS_DIR/cleanup-child-agent.sh" "$CHILD_NAME") >/dev/null 2>&1 || true
    echo "Error: failed to persist the registered child token" >&2
    exit 1
fi
'''
replace_once(old, new)

# Replace the hand-written failure cleanup with Phase 5b's validated common
# cleanup. A refused cleanup leaves owner/worktree state for recovery.
start = text.index("cleanup_on_failure() {\n", text.index("# --- 失敗時cleanup trap ---"))
end_marker = "trap cleanup_on_failure EXIT\n"
end = text.index(end_marker, start) + len(end_marker)
cleanup = r'''cleanup_on_failure() {
    if [[ "$SPAWN_COMPLETED" == true ]]; then
        return
    fi
    warn_if_uninjected
    if [[ "$CHILD_SESSION_STARTED" == true && -n "${CHILD_NAME:-}" ]]; then
        tmux kill-session -t "=$CHILD_NAME" >/dev/null 2>&1 || true
    fi
    if [[ -n "${CHILD_NAME:-}" ]]; then
        cleanup_dir="$WORK_DIR"
        if ! (cd "$cleanup_dir" && /bin/bash "$HOOKS_DIR/cleanup-child-agent.sh" "$CHILD_NAME"); then
            echo "[spawn_child] validated cleanup refused; preserving child state/worktree for recovery" >&2
            return
        fi
    fi
    cleanup_worktree
}
trap cleanup_on_failure EXIT
'''
text = text[:start] + cleanup + text[end:]

# Let the trap own conflict cleanup; duplicate release/retire logic used an
# ambient namespace and could delete state before owner revalidation.
start = text.index('    if [[ "$HAS_CONFLICT" == "yes" ]]; then\n')
end = text.index('    fi\nfi\n\n# --- 2c.', start) + len('    fi\n')
conflict = '''    if [[ "$HAS_CONFLICT" == "yes" ]]; then
        echo "Error: resource conflict detected; aborting spawn." >&2
        exit 21
    fi
'''
text = text[:start] + conflict + text[end:]

# A linked worktree shares repository ownership but has a new work_dir/root.
anchor = '''    WORK_DIR="$WORKTREE_DIR"
    echo "[spawn_child] WORK_DIR overridden to worktree: $WORK_DIR" >&2
'''
replacement = '''    WORK_DIR="$WORKTREE_DIR"
    if ! ags_apply_owned_workspace "$CHILD_NAME" "$WORK_DIR" "$CHILD_TOKEN_FILE"; then
        echo "[spawn_child] created worktree does not match child ownership" >&2
        exit 1
    fi
    unset AGS_OWNED_REGISTRATION_TOKEN
    PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"
    echo "[spawn_child] WORK_DIR overridden to worktree: $WORK_DIR" >&2
'''
# There are pre-registered and direct copies; alter the direct one only.
pos = text.index("# --- 2c. --worktree")
idx = text.index(anchor, pos)
text = text[:idx] + text[idx:].replace(anchor, replacement, 1)

# Direct tmux gets the complete validated tuple. The marker is metadata only;
# child bootstrap/cleanup still prove durable ownership.
anchor = 'TMUX_ENV_ARGS=(-e "CLAUDECODE=1" -e "AGENTSTACK_RESERVED_IDENTITY=1" -e "AGENT_NAME=$CHILD_NAME" -e "PARENT_AGENT=$PARENT_NAME" -e "PROJECT_KEY=$PROJECT_KEY" -e "AGENTSTACK_PROJECT_KEY=$PROJECT_KEY" -e "AGENTSTACK_HOOKS_DIR=$HOOKS_DIR"'
replacement = 'TMUX_ENV_ARGS=(-e "CLAUDECODE=1" -e "AGENTSTACK_RESERVED_IDENTITY=1" -e "AGENT_NAME=$CHILD_NAME" -e "PARENT_AGENT=$PARENT_NAME" -e "PROJECT_KEY=$PROJECT_KEY" -e "AGENTSTACK_PROJECT_KEY=$PROJECT_KEY" -e "AGENTSTACK_PROJECT_CONTEXT=1" -e "AGENTSTACK_PROJECT_REPOSITORY=${AGENTSTACK_PROJECT_REPOSITORY:-}" -e "AGENTSTACK_PROJECT_WORK_DIR=${AGENTSTACK_PROJECT_WORK_DIR:-}" -e "AGENTSTACK_PROJECT_WORKTREE_ROOT=${AGENTSTACK_PROJECT_WORKTREE_ROOT:-}" -e "AGENTSTACK_PROTECTED_ROOTS=${AGENTSTACK_PROTECTED_ROOTS:-}" -e "AGENTSTACK_PROJECT_CONTEXT_JSON=${AGENTSTACK_PROJECT_CONTEXT_JSON:-}" -e "AGENTSTACK_HOOKS_DIR=$HOOKS_DIR"'
# Pre-registered copy already carries context from Phase 5c; this exact shorter
# direct prefix should now occur once.
if text.count(anchor) != 1:
    raise SystemExit(f"direct tmux prefix count={text.count(anchor)}")
text = text.replace(anchor, replacement, 1)

path.write_text(text, encoding="utf-8")

# Focused regression for the direct legacy surface.
test = root / "tests/test_phase5_direct_child_ownership.py"
test.write_text(r'''from pathlib import Path
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
''', encoding="utf-8")

for doc in ("docs/launchers.md", "docs/launchers.en.md"):
    p = root / doc
    body = p.read_text(encoding="utf-8")
    if "### Direct delegated child ownership" not in body:
        note = (
            "\n\n### Direct delegated child ownership\n\n"
            + ("The legacy direct Claude/Codex spawn path validates the parent against the actual child workspace before any Mail side effect, claims the child name locally before registration, and publishes the same strong owner record used by pre-registered children. Linked worktrees refresh the child workspace context before tmux starts. Failure cleanup delegates release, retirement, and local identity deletion to the shared validated cleanup path.\n"
               if doc.endswith(".en.md") else
               "legacy direct Claude/Codex spawn pathも、Mail side effectより前に実child workspaceとparent ownershipを検証し、register前にchild nameをlocal claimし、pre-registered childと同じstrong owner recordをpublishします。linked worktreeではtmux起動前にchild workspace contextを更新し、失敗時のrelease・retire・local identity削除は共通validated cleanupへ委譲します。\n")
        )
        p.write_text(body + note, encoding="utf-8")

print("prepared phase5e")
