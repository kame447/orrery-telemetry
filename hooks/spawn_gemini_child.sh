#!/usr/bin/env bash
# spawn_gemini_child.sh — launch one delegated Google Antigravity child in an
# isolated git worktree, with ORRERY Mail identity/reservations bound by a local
# stdio proxy. The child task itself is streamed over stdin so it never appears
# in a process argv.
#
# Usage:
#   spawn_gemini_child.sh --resources "src/**,tests/**" "<task>" [WORKDIR]
#   spawn_gemini_child.sh --model gemini-3.8-flash-high --effort high \
#     --resources "docs/**" "<task>" [WORKDIR]
#
# Delegated Gemini children always use a worktree. This keeps the child-specific
# .agents/mcp_config.json out of the source checkout and matches ORRERY's
# one-agent/one-worktree isolation discipline.
set -euo pipefail

PROG="spawn_gemini_child.sh"
HOOKS_DIR="${AGENTSTACK_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
AGENTSTACK_HOME_DIR="${AGENTSTACK_HOME:-}"
if [[ -z "$AGENTSTACK_HOME_DIR" ]]; then
  AGENTSTACK_HOME_DIR="$(cd "$HOOKS_DIR/.." && pwd)"
fi
[[ -f "$AGENTSTACK_HOME_DIR/env.sh" ]] && . "$AGENTSTACK_HOME_DIR/env.sh"

PROJECT_KEY="${AGENTSTACK_PROJECT_KEY:-${PROJECT_KEY:-}}"
MCP_URL="${AGENTSTACK_MCP_URL:-http://127.0.0.1:18765/mcp}"
MAIL_ENV="${AGENTSTACK_MAIL_ENV:-$HOME/.agentstack/mail/.env}"
HTTP_BEARER_MODE="${AGENTSTACK_MAIL_HTTP_BEARER_MODE:-auto}"
RUNTIME_DIR="${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
MANAGED_FILE="${AGENTSTACK_MANAGED_AGENTS_FILE:-$RUNTIME_DIR/managed_agents.txt}"
GEMINI_BIN="${AGENTSTACK_GEMINI_BIN:-agy}"
MODEL="${AGENTSTACK_GEMINI_MODEL:-gemini-3.8-flash-high}"
EFFORT="${AGENTSTACK_GEMINI_EFFORT:-high}"
PRINT_TIMEOUT="${AGENTSTACK_GEMINI_PRINT_TIMEOUT:-30m}"
RESOURCE_TTL="${AGENTSTACK_GEMINI_RESOURCE_TTL:-14400}"
WORKTREE_ROOT="${AGENTSTACK_WORKTREE_ROOT:-/tmp/cc-worktrees}"
WORKTREE_BASE_REV=""
RESOURCES=""
UNSAFE_NO_RESOURCES=false

PREREGISTER="$AGENTSTACK_HOME_DIR/bin/agentstack-preregister-child"
MAIL_HELPER="$AGENTSTACK_HOME_DIR/bin/agentstack-gemini-child-mail"
STREAM_HELPER="$AGENTSTACK_HOME_DIR/bin/agentstack-gemini-stream"
PROXY_RUNNER="${AGENTSTACK_MCP_PROXY:-$AGENTSTACK_HOME_DIR/integrations/codex_app/plugin/scripts/run-mcp.sh}"
CLEANUP_HELPER="$HOOKS_DIR/cleanup-child-agent.sh"

usage() {
  cat >&2 <<'EOF'
Usage: spawn_gemini_child.sh [options] --resources "path1,path2" "<task>" [WORKDIR]

Options:
  --model MODEL           Antigravity model slug (default gemini-3.8-flash-high)
  --effort LEVEL          low, medium, or high (default high)
  --resources CSV         resources to reserve before launch (required)
  --resource-ttl SEC      reservation TTL (default 14400)
  --worktree-base REV     worktree base revision (default current HEAD)
  --unsafe-no-resources   explicit opt-out from reservations
EOF
}

while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --model)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      MODEL="$2"; shift 2 ;;
    --effort)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      EFFORT="$2"; shift 2 ;;
    --resources)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      RESOURCES="$2"; shift 2 ;;
    --resource-ttl)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      RESOURCE_TTL="$2"; shift 2 ;;
    --worktree-base)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      WORKTREE_BASE_REV="$2"; shift 2 ;;
    --unsafe-no-resources)
      UNSAFE_NO_RESOURCES=true; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "$PROG: unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

TASK="${1:-}"
WORK_DIR="${2:-$PWD}"
[[ -n "$TASK" ]] || { usage; exit 2; }
[[ -d "$WORK_DIR" ]] || { echo "$PROG: workdir does not exist: $WORK_DIR" >&2; exit 2; }
[[ -n "$PROJECT_KEY" ]] || { echo "$PROG: AGENTSTACK_PROJECT_KEY is required" >&2; exit 2; }
if [[ -z "$RESOURCES" && "$UNSAFE_NO_RESOURCES" != true ]]; then
  echo "$PROG: --resources or --unsafe-no-resources is required" >&2
  exit 2
fi
case "$EFFORT" in
  low|medium|high) ;;
  *) echo "$PROG: invalid effort '$EFFORT' (expected low, medium, or high)" >&2; exit 2 ;;
esac
case "$RESOURCE_TTL" in
  ''|*[!0-9]*) echo "$PROG: --resource-ttl must be an integer" >&2; exit 2 ;;
esac

command -v tmux >/dev/null 2>&1 || { echo "$PROG: tmux not found" >&2; exit 1; }
command -v "$GEMINI_BIN" >/dev/null 2>&1 || { echo "$PROG: Antigravity CLI not found (expected agy)" >&2; exit 1; }
[[ -x "$PREREGISTER" ]] || { echo "$PROG: missing $PREREGISTER" >&2; exit 1; }
[[ -x "$MAIL_HELPER" ]] || { echo "$PROG: missing $MAIL_HELPER" >&2; exit 1; }
[[ -x "$STREAM_HELPER" ]] || { echo "$PROG: missing $STREAM_HELPER" >&2; exit 1; }
[[ -x "$PROXY_RUNNER" ]] || { echo "$PROG: missing MCP proxy runner: $PROXY_RUNNER" >&2; exit 1; }

if [[ -n "${PARENT_AGENT:-}" ]]; then
  PARENT_NAME="$PARENT_AGENT"
elif [[ -n "${TMUX:-}" ]]; then
  PARENT_NAME="$(tmux display-message -p '#S' 2>/dev/null || true)"
else
  PARENT_NAME=""
fi
[[ -n "$PARENT_NAME" ]] || { echo "$PROG: parent agent is unknown; set PARENT_AGENT or run inside tmux" >&2; exit 1; }

SOURCE_REPO="$(git -C "$WORK_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$SOURCE_REPO" ]] || { echo "$PROG: delegated Gemini children require a git repository" >&2; exit 1; }
if [[ -n "$WORKTREE_BASE_REV" ]]; then
  BASE_REV="$(git -C "$SOURCE_REPO" rev-parse --verify "$WORKTREE_BASE_REV^{commit}" 2>/dev/null || true)"
else
  BASE_REV="$(git -C "$SOURCE_REPO" rev-parse --verify HEAD 2>/dev/null || true)"
fi
[[ -n "$BASE_REV" ]] || { echo "$PROG: could not resolve worktree base" >&2; exit 1; }

mkdir -p "$RUNTIME_DIR" "$WORKTREE_ROOT"
chmod 700 "$RUNTIME_DIR" 2>/dev/null || true
TOKEN_FILE="$RUNTIME_DIR/gemini-preregister-$$.token"
TASK_EVENT_FILE="$RUNTIME_DIR/gemini-task-$$.ndjson"
RUNNER_FILE="$RUNTIME_DIR/gemini-runner-$$.sh"
RESULT_LOG=""
STDERR_LOG=""
CHILD_NAME=""
WORKTREE_DIR=""
BRANCH_NAME=""
RESERVED=false
WORKTREE_CREATED=false

cleanup_failure() {
  status=$?
  if [[ $status -ne 0 ]]; then
    if [[ "$RESERVED" == true && -n "$CHILD_NAME" && -f "$TOKEN_FILE" && -n "$RESOURCES" ]]; then
      AGENTSTACK_HOME="$AGENTSTACK_HOME_DIR" \
      AGENTSTACK_MCP_URL="$MCP_URL" \
      AGENTSTACK_MAIL_ENV="$MAIL_ENV" \
      AGENTSTACK_MAIL_HTTP_BEARER_MODE="$HTTP_BEARER_MODE" \
        "$MAIL_HELPER" release --project-key "$PROJECT_KEY" --agent-name "$CHILD_NAME" \
          --token-file "$TOKEN_FILE" --paths "$RESOURCES" >/dev/null 2>&1 || true
    fi
    if [[ "$WORKTREE_CREATED" == true && -n "$WORKTREE_DIR" ]]; then
      git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1 || true
      [[ -n "$BRANCH_NAME" ]] && git -C "$SOURCE_REPO" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
    fi
    rm -f "$TOKEN_FILE" "$TASK_EVENT_FILE" "$RUNNER_FILE"
  fi
}
trap cleanup_failure EXIT

CHILD_NAME="$(
  AGENTSTACK_HOME="$AGENTSTACK_HOME_DIR" \
  AGENTSTACK_PROJECT_KEY="$PROJECT_KEY" \
  AGENTSTACK_MCP_URL="$MCP_URL" \
  AGENTSTACK_MAIL_ENV="$MAIL_ENV" \
  AGENTSTACK_MAIL_HTTP_BEARER_MODE="$HTTP_BEARER_MODE" \
    "$PREREGISTER" --project-key "$PROJECT_KEY" --program antigravity \
      --model "$MODEL" --task-description "Delegated Antigravity child" \
      --token-file-out "$TOKEN_FILE"
)"
[[ -n "$CHILD_NAME" ]] || { echo "$PROG: child preregistration returned no name" >&2; exit 1; }

BRANCH_NAME="exp/$CHILD_NAME"
WORKTREE_DIR="$WORKTREE_ROOT/$CHILD_NAME"
if [[ -e "$WORKTREE_DIR" ]]; then
  echo "$PROG: worktree path already exists: $WORKTREE_DIR" >&2
  exit 1
fi
if git -C "$SOURCE_REPO" show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
  echo "$PROG: child branch already exists: $BRANCH_NAME" >&2
  exit 1
fi
git -C "$SOURCE_REPO" worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" "$BASE_REV" >/dev/null
WORKTREE_CREATED=true

if [[ -n "$RESOURCES" ]]; then
  AGENTSTACK_HOME="$AGENTSTACK_HOME_DIR" \
  AGENTSTACK_MCP_URL="$MCP_URL" \
  AGENTSTACK_MAIL_ENV="$MAIL_ENV" \
  AGENTSTACK_MAIL_HTTP_BEARER_MODE="$HTTP_BEARER_MODE" \
    "$MAIL_HELPER" reserve --project-key "$PROJECT_KEY" --agent-name "$CHILD_NAME" \
      --token-file "$TOKEN_FILE" --paths "$RESOURCES" --ttl "$RESOURCE_TTL"
  RESERVED=true
fi

if git -C "$WORKTREE_DIR" ls-files --error-unmatch .agents/mcp_config.json >/dev/null 2>&1; then
  echo "$PROG: tracked .agents/mcp_config.json exists; refusing to overwrite project-owned Antigravity config" >&2
  exit 1
fi
mkdir -p "$WORKTREE_DIR/.agents"
MCP_CONFIG="$WORKTREE_DIR/.agents/mcp_config.json"
AGS_GEMINI_MCP_PATH="$MCP_CONFIG" \
AGS_GEMINI_PROXY_RUNNER="$PROXY_RUNNER" \
AGS_GEMINI_CHILD_NAME="$CHILD_NAME" \
AGS_GEMINI_TOKEN_FILE="$TOKEN_FILE" \
AGS_GEMINI_PROJECT_KEY="$PROJECT_KEY" \
AGS_GEMINI_MCP_URL="$MCP_URL" \
AGS_GEMINI_MAIL_ENV="$MAIL_ENV" \
AGS_GEMINI_BEARER_MODE="$HTTP_BEARER_MODE" \
AGS_GEMINI_RUNTIME_DIR="$RUNTIME_DIR" \
python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["AGS_GEMINI_MCP_PATH"])
entry = {
    "command": os.environ["AGS_GEMINI_PROXY_RUNNER"],
    "args": [],
    "env": {
        "AGENTSTACK_PROXY_AGENT_NAME": os.environ["AGS_GEMINI_CHILD_NAME"],
        "AGENTSTACK_PROXY_TOKEN_FILE": os.environ["AGS_GEMINI_TOKEN_FILE"],
        "AGENTSTACK_PROXY_PROGRAM": "antigravity",
        "AGENTSTACK_PROJECT_KEY": os.environ["AGS_GEMINI_PROJECT_KEY"],
        "AGENTSTACK_MCP_URL": os.environ["AGS_GEMINI_MCP_URL"],
        "AGENTSTACK_MAIL_ENV": os.environ["AGS_GEMINI_MAIL_ENV"],
        "AGENTSTACK_MAIL_HTTP_BEARER_MODE": os.environ["AGS_GEMINI_BEARER_MODE"],
        "AGENTSTACK_RUNTIME_DIR": os.environ["AGS_GEMINI_RUNTIME_DIR"],
        "AGENTSTACK_CODEX_APP_RUNTIME_DIR": os.path.join(
            os.environ["AGS_GEMINI_RUNTIME_DIR"],
            "gemini-proxy-" + os.environ["AGS_GEMINI_CHILD_NAME"],
        ),
    },
}
path.write_text(
    json.dumps({"mcpServers": {"orrery-mail": entry}}, indent=2) + "\n",
    encoding="utf-8",
)
os.chmod(path, 0o600)
PY

printf '%s' "$TASK" | python3 - "$TASK_EVENT_FILE" "$CHILD_NAME" "$PARENT_NAME" "$RESOURCES" <<'PY'
import json
import os
import sys

path, child, parent, resources = sys.argv[1:5]
task = sys.stdin.read()
prefix = (
    f"You are {child}, a delegated Antigravity child of {parent}. "
    "Work only in this isolated worktree. The launcher already reserved your declared resources. "
    "Do not register another ORRERY identity. If the ORRERY Mail MCP tools are permitted, you may use them for coordination; "
    "the launcher will report your final textual result to the parent automatically. "
    f"Declared resources: {resources or '(explicitly none)'}.\n\nCanonical task:\n"
)
message = {"event": "user", "message": {"content": prefix + task}}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(message, handle, ensure_ascii=False, separators=(",", ":"))
    handle.write("\n")
os.chmod(path, 0o600)
PY

RESULT_LOG="$RUNTIME_DIR/gemini-$CHILD_NAME.ndjson"
STDERR_LOG="$RUNTIME_DIR/gemini-$CHILD_NAME.stderr.log"

# The runner contains only paths and public identifiers. The owner token remains
# in TOKEN_FILE and is read by the proxy/mail helper when needed.
cat > "$RUNNER_FILE" <<EOF
#!/usr/bin/env bash
set -uo pipefail
cd $(printf '%q' "$WORKTREE_DIR") || exit 1
export AGENT_NAME=$(printf '%q' "$CHILD_NAME")
export PARENT_AGENT=$(printf '%q' "$PARENT_NAME")
export AGENTSTACK_RESERVED_IDENTITY=1
export AGENTSTACK_PROJECT_KEY=$(printf '%q' "$PROJECT_KEY")
export AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR")
export AGENTSTACK_HOOKS_DIR=$(printf '%q' "$HOOKS_DIR")
export AGENTSTACK_RUNTIME_DIR=$(printf '%q' "$RUNTIME_DIR")
export AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL")
export AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV")
export AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE")

set +e
cat $(printf '%q' "$TASK_EVENT_FILE") | \
  $(printf '%q' "$GEMINI_BIN") --input-format stream-json --output-format stream-json \
    --model $(printf '%q' "$MODEL") --effort $(printf '%q' "$EFFORT") \
    --print-timeout $(printf '%q' "$PRINT_TIMEOUT") \
    2> >(tee $(printf '%q' "$STDERR_LOG") >&2) | \
  $(printf '%q' "$STREAM_HELPER") $(printf '%q' "$RESULT_LOG")
agy_status=\${PIPESTATUS[1]}
set -e

AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR") \
AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL") \
AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV") \
AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE") \
  $(printf '%q' "$MAIL_HELPER") report --project-key $(printf '%q' "$PROJECT_KEY") \
    --agent-name $(printf '%q' "$CHILD_NAME") --token-file $(printf '%q' "$TOKEN_FILE") \
    --parent $(printf '%q' "$PARENT_NAME") --result-log $(printf '%q' "$RESULT_LOG") \
    --worktree $(printf '%q' "$WORKTREE_DIR") || true

if [[ -n $(printf '%q' "$RESOURCES") ]]; then
  AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR") \
  AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL") \
  AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV") \
  AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE") \
    $(printf '%q' "$MAIL_HELPER") release --project-key $(printf '%q' "$PROJECT_KEY") \
      --agent-name $(printf '%q' "$CHILD_NAME") --token-file $(printf '%q' "$TOKEN_FILE") \
      --paths $(printf '%q' "$RESOURCES") || true
fi

[[ -x $(printf '%q' "$CLEANUP_HELPER") ]] && $(printf '%q' "$CLEANUP_HELPER") || true
rm -f $(printf '%q' "$TASK_EVENT_FILE") $(printf '%q' "$TOKEN_FILE") $(printf '%q' "$MCP_CONFIG") $(printf '%q' "$RUNNER_FILE")
echo "[antigravity] child finished; worktree retained at $(printf '%q' "$WORKTREE_DIR")"
exit "\$agy_status"
EOF
chmod 700 "$RUNNER_FILE"

tmux new-session -d -s "$CHILD_NAME" -c "$WORKTREE_DIR" \
  -e "AGENT_NAME=$CHILD_NAME" -e "PARENT_AGENT=$PARENT_NAME" \
  -e "AGENTSTACK_RESERVED_IDENTITY=1" \
  "/bin/bash $(printf '%q' "$RUNNER_FILE")"

mkdir -p "$(dirname "$MANAGED_FILE")"
if ! grep -qxF "$CHILD_NAME" "$MANAGED_FILE" 2>/dev/null; then
  printf '%s\n' "$CHILD_NAME" >> "$MANAGED_FILE"
fi

trap - EXIT
echo "[spawn_gemini_child] launched $CHILD_NAME" >&2
echo "[spawn_gemini_child] worktree: $WORKTREE_DIR (branch $BRANCH_NAME, base ${BASE_REV:0:12})" >&2
echo "[spawn_gemini_child] result log: $RESULT_LOG" >&2
printf '%s\n' "$CHILD_NAME"
