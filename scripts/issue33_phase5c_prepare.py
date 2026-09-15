from __future__ import annotations

import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()


def replace_once(path: str, old: str, new: str) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected one anchor, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before(path: str, anchor: str, addition: str) -> None:
    replace_once(path, anchor, addition + anchor)


# Shared handoff proof: strong child owner wins; otherwise a validated parent
# workspace plus the child's Mail token proves an ownerless one-shot handoff.
register_addition = r'''# Validate the workspace and namespace for a child that Mail already registered.
# Existing child ownership is authoritative and may never downgrade to the
# parent. An ownerless one-shot handoff is accepted only when the parent owns
# the actual workspace and Mail authenticates the child token in that namespace.
ags_prepare_preregistered_handoff() {
  local child_name="$1" work_dir="$2" token_file="${3:-}"
  local parent_name="${4:-}" supplied_project="${5:-}"
  local token="" context="" project="" local_child_state=0

  [[ "$child_name" =~ ^[A-Za-z0-9_.-]+$ ]] || return 1
  [[ -d "$work_dir" ]] || return 1
  if ags_registration_owner_exists_for_name "$child_name" || \
     ags_legacy_registration_owner_exists_for_name "$child_name"; then
    local_child_state=1
  fi

  if [[ -n "$token_file" ]]; then
    token="$(ags_read_private_registration_token "$token_file")" || return 1
  fi

  if [[ "$local_child_state" == "1" ]]; then
    # A contradictory durable/legacy child claim is terminal: never borrow the
    # parent's valid owner to revive a different child generation.
    ags_apply_owned_workspace "$child_name" "$work_dir" "$token_file" "$token" \
      || return 1
    context="${AGENTSTACK_PROJECT_CONTEXT_JSON:-}"
    [[ -n "$token" ]] || token="${AGS_OWNED_REGISTRATION_TOKEN:-}"
    unset AGS_OWNED_REGISTRATION_TOKEN
  else
    [[ -n "$parent_name" && -n "$token" ]] || return 1
    ags_apply_owned_workspace "$parent_name" "$work_dir" || return 1
    context="${AGENTSTACK_PROJECT_CONTEXT_JSON:-}"
    unset AGS_OWNED_REGISTRATION_TOKEN
    project="$(ags_registration_context_project_key "$context")" || return 1
    ags_verify_registration_owner_with_mail "$project" "$child_name" "$token" \
      || return 1
  fi

  [[ -n "$context" && -n "$token" ]] || return 1
  project="$(ags_registration_context_project_key "$context")" || return 1
  if [[ -n "$supplied_project" ]] && \
     ! ags_project_keys_equal "$supplied_project" "$project"; then
    echo "agentstack: pre-registered handoff project '$supplied_project' does not match owned workspace '$project'." >&2
    return 1
  fi
  agentstack_export_context_json "$context" || return 1
  AGS_PREREGISTERED_CONTEXT_JSON="$context"
  AGS_PREREGISTERED_REGISTRATION_TOKEN="$token"
  AGS_PREREGISTERED_OWNER_PREEXISTED="$local_child_state"
}

'''
insert_before(
    "bin/lib/agentstack-register.sh",
    "ags_local_agent_name_conflicts() {\n",
    register_addition,
)

# Bind the intended workdir before embedding the task or consuming the token.
spawn_anchor = '''    EMBEDDED_TASK_PROMPT=""\n'''
spawn_validation = r'''    PRE_REGISTERED_SOURCE_TOKEN_FILE="$CHILD_TOKEN_FILE"
    if ! ags_prepare_preregistered_handoff "$CHILD_NAME" "$WORK_DIR" \
        "$PRE_REGISTERED_SOURCE_TOKEN_FILE" "$PARENT_NAME" "$PROJECT_KEY"; then
        echo "Error: pre-registered child handoff does not own this workspace/project" >&2
        exit 1
    fi
    PRE_REGISTERED_CONTEXT_JSON="$AGS_PREREGISTERED_CONTEXT_JSON"
    PRE_REGISTERED_HANDOFF_TOKEN="$AGS_PREREGISTERED_REGISTRATION_TOKEN"
    PRE_REGISTERED_OWNER_PREEXISTED="$AGS_PREREGISTERED_OWNER_PREEXISTED"
    unset AGS_PREREGISTERED_CONTEXT_JSON AGS_PREREGISTERED_REGISTRATION_TOKEN
    unset AGS_PREREGISTERED_OWNER_PREEXISTED
    PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"

'''
insert_before("hooks/spawn_child.sh", spawn_anchor, spawn_validation)

# Failure cleanup must use the same validated owner and real workspace. If the
# ownership proof no longer matches, preserve local state/worktree for recovery.
spawn_path = root / "hooks/spawn_child.sh"
spawn = spawn_path.read_text(encoding="utf-8")
start = spawn.index("    cleanup_preregister_failure() {\n", spawn.index("# --- Pre-registered mode ---"))
end_marker = "    trap cleanup_preregister_failure EXIT\n"
end = spawn.index(end_marker, start) + len(end_marker)
cleanup_replacement = r'''    cleanup_preregister_failure() {
        if [[ "$PRE_REGISTERED_SUCCESS" == true ]]; then
            return
        fi
        warn_if_uninjected
        if [[ "$PRE_REGISTERED_SESSION_STARTED" == true ]]; then
            tmux kill-session -t "=$CHILD_NAME" >/dev/null 2>&1 || true
        fi
        if [[ -n "${PRE_REGISTERED_HANDOFF_TOKEN:-}" ]]; then
            if ! (
                cd "$WORK_DIR" && \
                CHILD_REGISTRATION_TOKEN="$PRE_REGISTERED_HANDOFF_TOKEN" \
                /bin/bash "$HOOKS_DIR/cleanup-child-agent.sh" "$CHILD_NAME"
            ); then
                echo "[spawn_child/pre-reg] cleanup ownership changed; leaving child state/worktree for recovery" >&2
                return
            fi
        fi
        cleanup_worktree
    }
    trap cleanup_preregister_failure EXIT
'''
spawn = spawn[:start] + cleanup_replacement + spawn[end:]
spawn_path.write_text(spawn, encoding="utf-8")

# Publish a strong child owner after the one-shot token has been durably adopted.
owner_publish_anchor = '''\n    # --worktree が指定されていれば worktree を作って WORK_DIR を上書き\n'''
owner_publish = r'''
    if [[ "$PRE_REGISTERED_OWNER_PREEXISTED" != "1" ]]; then
        if ! ags_store_registration_token "$CHILD_NAME" \
            "$PRE_REGISTERED_HANDOFF_TOKEN" "$PRE_REGISTERED_CONTEXT_JSON" \
            preregister-child; then
            echo "Error: could not publish durable ownership for pre-registered child $CHILD_NAME" >&2
            exit 1
        fi
    fi
'''
insert_before("hooks/spawn_child.sh", owner_publish_anchor, owner_publish)

# A new linked worktree keeps repository ownership but changes work_dir/root.
worktree_anchor = '''        WORK_DIR="$WORKTREE_DIR"\n        echo "[spawn_child/pre-reg] WORK_DIR overridden to worktree: $WORK_DIR" >&2\n'''
worktree_replacement = r'''        WORK_DIR="$WORKTREE_DIR"
        if ! ags_apply_owned_workspace "$CHILD_NAME" "$WORK_DIR"; then
            echo "Error: created worktree is not owned by pre-registered child $CHILD_NAME" >&2
            exit 1
        fi
        PRE_REGISTERED_CONTEXT_JSON="$AGENTSTACK_PROJECT_CONTEXT_JSON"
        PRE_REGISTERED_HANDOFF_TOKEN="$AGS_OWNED_REGISTRATION_TOKEN"
        unset AGS_OWNED_REGISTRATION_TOKEN
        PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"
        echo "[spawn_child/pre-reg] WORK_DIR overridden to worktree: $WORK_DIR" >&2
'''
replace_once("hooks/spawn_child.sh", worktree_anchor, worktree_replacement)

# Pass the complete validated tuple to the child session as metadata; bootstrap
# still revalidates durable ownership and does not trust the marker by itself.
spawn = spawn_path.read_text(encoding="utf-8")
pre = spawn.index("# --- Pre-registered mode ---")
pos = spawn.index('    TMUX_ENV_ARGS=(-e "CLAUDECODE=1"', pre)
line_end = spawn.index("\n", pos)
new_line = r'''    TMUX_ENV_ARGS=(-e "CLAUDECODE=1" -e "AGENTSTACK_RESERVED_IDENTITY=1" -e "AGENT_NAME=$CHILD_NAME" -e "PROJECT_KEY=$PROJECT_KEY" -e "AGENTSTACK_PROJECT_KEY=$PROJECT_KEY" -e "AGENTSTACK_PROJECT_CONTEXT=1" -e "AGENTSTACK_PROJECT_REPOSITORY=${AGENTSTACK_PROJECT_REPOSITORY:-}" -e "AGENTSTACK_PROJECT_WORK_DIR=${AGENTSTACK_PROJECT_WORK_DIR:-}" -e "AGENTSTACK_PROJECT_WORKTREE_ROOT=${AGENTSTACK_PROJECT_WORKTREE_ROOT:-}" -e "AGENTSTACK_PROTECTED_ROOTS=${AGENTSTACK_PROTECTED_ROOTS:-}" -e "AGENTSTACK_PROJECT_CONTEXT_JSON=${AGENTSTACK_PROJECT_CONTEXT_JSON:-}" -e "AGENTSTACK_HOOKS_DIR=$HOOKS_DIR" -e "AGENTSTACK_RUNTIME_DIR=$RUNTIME_DIR" -e "AGENTSTACK_MCP_URL=$MCP_URL" -e "AGENTSTACK_MAIL_ENV=$MAIL_ENV" -e "AGENTSTACK_MAIL_HTTP_BEARER_MODE=$HTTP_BEARER_MODE" -e "AGENTSTACK_TERMINAL=$TERMINAL_SETTING" -e "AGENTSTACK_CODEX_APPROVAL=$(codex_approval_flags)" -e "AGENTSTACK_CODEX_NETWORK_FLAGS=$(codex_network_flags)")'''
spawn_path.write_text(spawn[:pos] + new_line + spawn[line_end:], encoding="utf-8")

# The canonical /delegate preregistration binds the child to the intended
# workspace instead of the parent's ambient pwd.
skill_old = '''     --task-description "<task summary>" \\\n     --token-file-out "$CHILD_TOKEN_FILE")"\n'''
skill_new = '''     --task-description "<task summary>" \\\n     --work-dir "<working-directory>" \\\n     --token-file-out "$CHILD_TOKEN_FILE")"\n'''
replace_once("skills/delegate/SKILL.md", skill_old, skill_new)
insert_before(
    "skills/delegate/SKILL.md",
    "   The helper prints the registered name; use `$CHILD_NAME` from here on rather than a name you chose yourself.\n",
    "   `--work-dir` is part of the ownership proof: pass the same workspace that will be handed to `spawn_child.sh`; do not let preregistration default to the parent's ambient cwd.\n",
)

# Small behavior note; no new conceptual surface.
for doc in ("docs/launchers.md", "docs/launchers.en.md"):
    target = root / doc
    text = target.read_text(encoding="utf-8")
    if "### Pre-registered handoff ownership" not in text:
        if doc.endswith(".en.md"):
            addition = "\n\n### Pre-registered handoff ownership\n\nA pre-registered child is started only after its private token and actual workspace are tied to durable child ownership. For a Dashboard one-shot handoff with no local child owner yet, the parent must own that workspace and the child token must authenticate in the same Mail project. A contradictory child owner never falls back to the parent. Linked worktrees reuse repository ownership but refresh the child work directory before tmux starts.\n"
        else:
            addition = "\n\n### Pre-registered handoff ownership\n\n事前登録済みchildは、private tokenと実workspaceがdurableなchild ownershipに結び付く場合だけ起動します。Dashboardのone-shot handoffでchild ownerがまだ無い場合は、親がそのworkspaceを所有し、child tokenが同じMail projectで認証できる必要があります。矛盾するchild ownerを親ownerへfallbackしません。linked worktreeではrepository ownershipを維持しつつ、tmux起動前にchildのwork directoryを更新します。\n"
        target.write_text(text + addition, encoding="utf-8")

# Existing embedded-task fixtures now model the documented preregistration:
# publish a strong owner before handing the temporary token to the launcher.
test_path = root / "tests/test_spawn_child_embed_task.py"
test = test_path.read_text(encoding="utf-8")
helper_anchor = '''def test_embed_task_requires_pre_registered(tmp_path: pathlib.Path) -> None:\n'''
helper = r'''def _publish_child_owner(
    env: dict[str, str], workdir: pathlib.Path, child_name: str, token: str,
) -> None:
    command = (
        'set -euo pipefail; . "$1"; '
        'ctx=$(agentstack_resolve_invocation_context "$2" /shared/project); '
        'ags_store_registration_token "$3" "$4" "$ctx" preregister-child'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command, "fixture",
         str(ROOT / "bin/lib/agentstack-register.sh"), str(workdir), child_name, token],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


'''
if helper_anchor not in test:
    raise SystemExit("embed task helper anchor missing")
test = test.replace(helper_anchor, helper + helper_anchor, 1)
call_anchor = '''    child_name = "EmbedCodex" if codex else "EmbedClaude"\n\n    args = [\n'''
call_replacement = '''    child_name = "EmbedCodex" if codex else "EmbedClaude"\n    _publish_child_owner(env, workdir, child_name, "child-owner-token")\n\n    args = [\n'''
if test.count(call_anchor) != 1:
    raise SystemExit("embed owner call anchor missing")
test_path.write_text(test.replace(call_anchor, call_replacement, 1), encoding="utf-8")

# New focused tests exercise the helper contract independently of tmux/provider.
new_test = root / "tests/test_phase5_preregistered_handoff.py"
new_test.write_text(r'''from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "bin/lib/agentstack-register.sh"
SKILL = ROOT / "skills/delegate/SKILL.md"


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGENTSTACK_", "GIT_")) and k not in
           {"HOME", "PROJECT_KEY", "AGENT_NAME", "PARENT_AGENT",
            "CHILD_REGISTRATION_TOKEN", "MCP_AGENT_MAIL_TOKEN", "BASH_ENV", "ENV"}}
    home = tmp_path / "home"
    home.mkdir()
    env.update(HOME=str(home), AGENTSTACK_RUNTIME_DIR=str(tmp_path / "runtime"),
               AGENTSTACK_MAIL_HTTP_BEARER_MODE="disabled")
    return env


def _run(env: dict[str, str], cwd: Path, body: str, *args: str):
    script = (
        'set -euo pipefail; . "$1"; shift; '
        'MAIL_CALLS=${MAIL_CALLS:-}; '
        'ags_verify_registration_owner_with_mail(){ '
        'printf "%s|%s\\n" "$1" "$2" >> "$MAIL_CALLS"; return "${MAIL_VERIFY_RC:-0}"; }; '
        + body
    )
    return subprocess.run(["/bin/bash", "-c", script, "fixture", str(LIB), *args],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=25)


def _publish(env: dict[str, str], cwd: Path, name: str, token: str, project: str = "team-x"):
    result = _run(env, cwd,
        'ctx=$(agentstack_resolve_invocation_context "$PWD" "$1"); '
        'ags_store_registration_token "$2" "$3" "$ctx" preregister-child',
        project, name, token)
    assert result.returncode == 0, result.stderr


def test_strong_child_owner_authorizes_same_workspace_without_parent(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ChildCurie", "child-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o600)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" "" team-x; '
        'printf "%s\\n" "$AGENTSTACK_PROJECT_KEY"; printf "%s\\n" "$AGS_PREREGISTERED_OWNER_PREEXISTED"',
        str(one_shot))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["team-x", "1"]
    assert not Path(env["MAIL_CALLS"]).exists()


def test_contradictory_child_owner_never_downgrades_to_parent(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ParentCurie", "parent-token")
    _publish(env, work, "ChildCurie", "original-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("replacement-token"); one_shot.chmod(0o600)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x', str(one_shot))
    assert result.returncode != 0
    assert not Path(env["MAIL_CALLS"]).exists()


def test_ownerless_child_requires_parent_owner_and_mail_proof(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ParentCurie", "parent-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o600)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x; '
        'printf "%s\\n" "$AGENTSTACK_PROJECT_KEY"; printf "%s\\n" "$AGS_PREREGISTERED_OWNER_PREEXISTED"',
        str(one_shot))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["team-x", "0"]
    assert Path(env["MAIL_CALLS"]).read_text().strip() == "team-x|ChildCurie"


def test_ownerless_foreign_workspace_fails_before_child_mail_proof(tmp_path: Path):
    owned = tmp_path / "owned"; owned.mkdir()
    foreign = tmp_path / "foreign"; foreign.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, owned, "ParentCurie", "parent-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o600)
    result = _run(env, foreign,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x', str(one_shot))
    assert result.returncode != 0
    assert not Path(env["MAIL_CALLS"]).exists()


def test_insecure_one_shot_token_is_rejected(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ParentCurie", "parent-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o644)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x', str(one_shot))
    assert result.returncode != 0
    assert not Path(env["MAIL_CALLS"]).exists()


def test_delegate_preregistration_binds_the_declared_workdir():
    text = SKILL.read_text(encoding="utf-8")
    block = text.split('CHILD_NAME="$("${AGENTSTACK_PREREGISTER_CHILD', 1)[1].split(')"', 1)[0]
    assert '--work-dir "<working-directory>"' in block
''', encoding="utf-8")

print("prepared phase5c")
