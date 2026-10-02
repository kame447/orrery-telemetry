#!/usr/bin/env bash
# Shared helpers for the agent-start / agent-start-codex launchers.
# SOURCE this file ( . agentstack-launch.sh ); it is not meant to be executed.
#
# Provides:
#   ags_load_env            source the installer-written env.sh (AGENTSTACK_*)
#   ags_die MSG             print error (prefixed with $AGS_PROG) and exit 1
#   ags_resolve_tmux        print the tmux binary path (or empty)
#   ags_abspath DIR         print the absolute path of DIR
#   ags_choose_dir [DIR]    resolve the working dir (arg / fzf picker / cwd)
#   ags_parse_top_level_args parse --project-key and top-level-only options
#   ags_prepare_top_level_launch show/validate the selected launch context
#
# Env it honours:
#   AGENTSTACK_HOME       install dir (default ~/.agentstack); env.sh lives here
#   AGENTSTACK_BASE_DIR   root the fzf picker browses (default $HOME)

AGS_PROG="${AGS_PROG:-agentstack}"

ags_die() { printf '%s: %s\n' "$AGS_PROG" "$*" >&2; exit 1; }

# Pull in installer-written AGENTSTACK_* values without letting them replace a
# setting chosen for this command: an explicit value wins over env.sh, which
# wins over the defaults below (issue #33). The order lives in
# hooks/project-context.sh so the installer and the launchers share it.
AGS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ags_load_env() {
  local envf="${AGENTSTACK_HOME:-$HOME/.agentstack}/env.sh"
  local ctx="$AGS_LIB_DIR/../../hooks/project-context.sh"
  [[ -f "$ctx" ]] || ctx="${AGENTSTACK_HOME:-$HOME/.agentstack}/hooks/project-context.sh"
  [[ -f "$ctx" ]] || ags_die "missing hooks/project-context.sh next to $AGS_LIB_DIR; re-run install.sh"
  # shellcheck source=../../hooks/project-context.sh
  . "$ctx"
  agentstack_load_top_level_env "$envf"
  AGS_PROJECT_CONTEXT_HELPER="$ctx"
}

ags_resolve_tmux() {
  local c
  for c in /opt/homebrew/bin/tmux /usr/local/bin/tmux; do
    [[ -x "$c" ]] && { printf '%s\n' "$c"; return 0; }
  done
  command -v tmux 2>/dev/null || true
}

ags_abspath() { (cd "$1" 2>/dev/null && pwd) || return 1; }

# fzf one-level directory navigator rooted at $AGENTSTACK_BASE_DIR.
#   Right: descend   Left: parent (stops at base)   Enter: select   Esc: cancel
ags_pick_dir() {
  local base current pick key sel children parent
  base="${AGENTSTACK_BASE_DIR:-$HOME}"
  base="$(ags_abspath "$base")" || ags_die "AGENTSTACK_BASE_DIR is not a directory: ${AGENTSTACK_BASE_DIR:-$HOME}"
  current="$base"
  while true; do
    pick="$(find "$current" -maxdepth 1 -type d -not -name '.*' | sort \
      | fzf --height 50% --reverse \
            --prompt="${current/#$HOME/\~}/ > " \
            --header="<-:parent  ->:enter  Enter:select  Esc:cancel" \
            --expect="right,left" --no-multi)" || true
    key="$(printf '%s\n' "$pick" | sed -n '1p')"
    sel="$(printf '%s\n' "$pick" | sed -n '2p')"
    [[ -z "$key" && -z "$sel" ]] && return 1   # Esc / empty -> cancel
    case "$key" in
      right)
        if [[ -n "$sel" && -d "$sel" ]]; then
          children="$(find "$sel" -maxdepth 1 -type d -not -name '.*' -not -path "$sel" 2>/dev/null | head -1)"
          [[ -n "$children" ]] && current="$sel"
        fi ;;
      left)
        parent="$(dirname "$current")"
        [[ "$current" != "$base" ]] && current="$parent" ;;
      *)
        [[ -n "$sel" ]] && { printf '%s\n' "$sel"; return 0; } ;;
    esac
  done
}

# Resolve the working directory: explicit arg > fzf picker > current dir.
# Prints the chosen absolute path on stdout. Returns nonzero only on cancel.
ags_choose_dir() {
  if [[ $# -gt 0 && -n "$1" ]]; then
    [[ -d "$1" ]] || ags_die "directory not found: $1"
    ags_abspath "$1"; return 0
  fi
  if command -v fzf >/dev/null 2>&1; then
    ags_pick_dir; return $?
  fi
  printf '%s\n' "$PWD"
  echo "$AGS_PROG: fzf not installed; using current directory ($PWD)" >&2
  echo "          pass a path ($AGS_PROG DIR) or install fzf to browse a vault" >&2
  return 0
}

# Parse options shared by the interactive top-level launchers. Child launchers
# and resume paths intentionally do not call this helper.
ags_parse_top_level_args() {
  AGS_EXPLICIT_PROJECT_KEY=""
  AGS_EXPLICIT_PROJECT_KEY_SET=0
  AGS_LAUNCH_DIR=""
  AGS_LAUNCH_DIR_SET=0
  AGS_LAUNCH_DRY_RUN=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project-key)
        [[ $# -ge 2 && -n "${2:-}" ]] || ags_die "--project-key requires a non-empty value"
        [[ "$AGS_EXPLICIT_PROJECT_KEY_SET" == 0 ]] || ags_die "--project-key specified more than once"
        AGS_EXPLICIT_PROJECT_KEY="$2"
        AGS_EXPLICIT_PROJECT_KEY_SET=1
        shift 2
        ;;
      --dry-run)
        [[ "$AGS_PROG" == "agent-start-gemini" ]] || ags_die "unknown option: $1"
        AGS_LAUNCH_DRY_RUN=true
        shift
        ;;
      --)
        shift
        [[ $# -le 1 ]] || ags_die "expected at most one directory"
        if [[ $# -eq 1 ]]; then
          [[ "$AGS_LAUNCH_DIR_SET" == 0 ]] || ags_die "expected at most one directory"
          AGS_LAUNCH_DIR="$1"
          AGS_LAUNCH_DIR_SET=1
          shift
        fi
        ;;
      -*)
        ags_die "unknown option: $1"
        ;;
      *)
        [[ "$AGS_LAUNCH_DIR_SET" == 0 ]] || ags_die "expected at most one directory"
        AGS_LAUNCH_DIR="$1"
        AGS_LAUNCH_DIR_SET=1
        shift
        ;;
    esac
  done
  if [[ "$AGS_LAUNCH_DIR_SET" == 1 ]]; then
    [[ -n "$AGS_LAUNCH_DIR" && -d "$AGS_LAUNCH_DIR" ]] || ags_die "directory not found: $AGS_LAUNCH_DIR"
  fi
}

ags_choose_top_level_dir() {
  if [[ "$AGS_LAUNCH_DIR_SET" == 1 ]]; then
    [[ -n "$AGS_LAUNCH_DIR" && -d "$AGS_LAUNCH_DIR" ]] || ags_die "directory not found: $AGS_LAUNCH_DIR"
    ags_choose_dir "$AGS_LAUNCH_DIR"
  else
    ags_choose_dir
  fi
}

# Apply --project-key after env.sh has been loaded, optionally require that
# explicit option, and show the effective context before registration. This
# keeps project namespace selection separate from actual workspace provenance.
ags_prepare_top_level_launch() {
  local work_dir="$1"
  local project_key=""
  local project_source="${AGS_PROJECT_KEY_SOURCE:-unset}"
  local protected_roots=""

  if [[ "$AGS_EXPLICIT_PROJECT_KEY_SET" == 1 ]]; then
    project_source="--project-key"
  fi

  if [[ "${AGENTSTACK_REQUIRE_EXPLICIT_PROJECT_KEY:-0}" == "1" &&
        "$AGS_EXPLICIT_PROJECT_KEY_SET" != 1 ]]; then
    printf '%s: --project-key is required because AGENTSTACK_REQUIRE_EXPLICIT_PROJECT_KEY=1\n' "$AGS_PROG" >&2
    printf 'usage: %s --project-key KEY [DIR]\n' "$AGS_PROG" >&2
    return 2
  fi

  ags_prepare_top_level_context "$work_dir" "${AGS_EXPLICIT_PROJECT_KEY:-}" || return 1
  project_key="$AGENTSTACK_PROJECT_KEY"
  work_dir="$AGENTSTACK_PROJECT_WORK_DIR"
  protected_roots="${AGENTSTACK_PROTECTED_ROOTS:-$AGENTSTACK_PROJECT_KEY}"
  [[ -n "$project_key" ]] || project_key="<unset>"
  [[ -n "$protected_roots" ]] || protected_roots="<unset>"

  printf '%s: launch context: project=%s source=%s\n' \
    "$AGS_PROG" "$project_key" "$project_source" >&2
  printf '%s: launch context: workdir=%s protected_roots=%s\n' \
    "$AGS_PROG" "$work_dir" "$protected_roots" >&2
}

# Keep launch-root resolution shared with child and resume boundaries.
ags_prepare_top_level_context() {
  if ! command -v agentstack_apply_workspace_context >/dev/null 2>&1; then
    # shellcheck source=../../hooks/project-context.sh
    . "${AGS_PROJECT_CONTEXT_HELPER:-$AGS_LIB_DIR/../../hooks/project-context.sh}" || return 1
  fi
  agentstack_apply_workspace_context "$1" "${2:-}"
}

# Emit shell-quoted tmux options outside the pane command's nested quoting.
# Session values override a pre-existing server's stale global environment.
ags_tmux_project_options() {
  local name
  for name in AGENTSTACK_PROJECT_KEY PROJECT_KEY AGENTSTACK_PROJECT_REPOSITORY \
    AGENTSTACK_PROJECT_WORK_DIR AGENTSTACK_PROJECT_WORKTREE_ROOT AGENTSTACK_PROTECTED_ROOTS AGENTSTACK_EXTRA_PROTECTED_ROOTS AGENTSTACK_PROTECTION_CONTEXT; do
    printf -- '-e %q ' "$name=${!name:-}"
  done
  printf '%s' '-e AGENTSTACK_PROJECT_CONTEXT= -e AGENTSTACK_LOOKUP_PROJECT_KEY='
}

# Mark only the session this launcher just created as removing the three Git
# selectors. Merely unsetting them in its first process would let later panes
# inherit the old server-global values again. Do not use set-environment -g.
ags_tmux_clear_git_command() {
  local session="$1" name separator=""
  for name in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR; do
    printf '%s%q set-environment -r -t %q %q' "$separator" "$TMUX_BIN" "=$session" "$name"
    separator=" && "
  done
}

# After capturing the session options, do not seed a new tmux server globally
# with this invocation's project. This changes only the launching process.
ags_clear_client_project_context() {
  unset AGENTSTACK_PROJECT_KEY PROJECT_KEY AGENTSTACK_PROJECT_REPOSITORY
  unset AGENTSTACK_PROJECT_WORK_DIR AGENTSTACK_PROJECT_WORKTREE_ROOT AGENTSTACK_PROTECTED_ROOTS AGENTSTACK_EXTRA_PROTECTED_ROOTS
  unset AGENTSTACK_PROJECT_CONTEXT AGENTSTACK_LOOKUP_PROJECT_KEY AGENTSTACK_PROTECTION_CONTEXT
}
