#!/usr/bin/env bash
# Dashboard adapter for a Gemini child that ORRERY Mail already registered.
# Accepts the same pre-registered contract used by dashboard/server.py, then
# launches Antigravity headless in an isolated worktree.
set -euo pipefail

PROG="spawn_gemini_preregistered.sh"
HOOKS_DIR="${AGENTSTACK_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
AGENTSTACK_HOME_DIR="${AGENTSTACK_HOME:-$(cd "$HOOKS_DIR/.." && pwd)}"
PROJECT_KEY="${AGENTSTACK_PROJECT_KEY:-${PROJECT_KEY:-}}"
MCP_URL="${AGENTSTACK_MCP_URL:-http://127.0.0.1:18765/mcp}"
MAIL_ENV="${AGENTSTACK_MAIL_ENV:-$HOME/.agentstack/mail/.env}"
HTTP_BEARER_MODE="${AGENTSTACK_MAIL_HTTP_BEARER_MODE:-auto}"
RUNTIME_DIR="${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
MANAGED_FILE="${AGENTSTACK_MANAGED_AGENTS_FILE:-$RUNTIME_DIR/managed_agents.txt}"
PYTHON_BIN="${AGENTSTACK_PYTHON:-python3}"
GEMINI_BIN="${AGENTSTACK_GEMINI_BIN:-agy}"
EFFORT="${AGENTSTACK_GEMINI_EFFORT:-high}"
RESOURCES="${AGENTSTACK_GEMINI_RESOURCES:-}"
TASK_FILE="${AGENTSTACK_GEMINI_TASK_FILE:-}"
PRINT_TIMEOUT="${AGENTSTACK_GEMINI_PRINT_TIMEOUT:-30m}"
RESOURCE_TTL="${AGENTSTACK_GEMINI_RESOURCE_TTL:-14400}"
WORKTREE_ROOT="${AGENTSTACK_WORKTREE_ROOT:-/tmp/cc-worktrees}"
WORKTREE_BASE_REV=""
CHILD_NAME=""
CHILD_TOKEN_FILE=""
MODEL="${AGENTSTACK_GEMINI_MODEL:-gemini-3.8-flash-high}"
WORK_DIR=""

MAIL_HELPER="$AGENTSTACK_HOME_DIR/bin/agentstack-gemini-child-mail"
STREAM_HELPER="$AGENTSTACK_HOME_DIR/bin/agentstack-gemini-stream"
PROXY_RUNNER="${AGENTSTACK_MCP_PROXY:-$AGENTSTACK_HOME_DIR/integrations/codex_app/plugin/scripts/run-mcp.sh}"
CLEANUP_HELPER="$HOOKS_DIR/cleanup-child-agent.sh"

usage() {
  cat >&2 <<'EOF'
Usage: spawn_gemini_preregistered.sh --pre-registered NAME --child-token-file FILE \
  [--worktree] [--worktree-base REV] --model MODEL [TASK_SHORT] [WORKDIR]
EOF
}

while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --pre-registered) CHILD_NAME="${2:-}"; shift 2 ;;
    --child-token-file|--token-file) CHILD_TOKEN_FILE="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --worktree) shift ;;
    --worktree-base) WORKTREE_BASE_REV="${2:-}"; shift 2 ;;
    --standalone)
      echo "$PROG: standalone Gemini launch is not supported by the dashboard adapter" >&2
      exit 2
      ;;
    *) echo "$PROG: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

TASK_SHORT="${1:-}"
WORK_DIR="${2:-$PWD}"
[[ -n "$CHILD_NAME" ]] || { usage; exit 2; }
[[ -n "$CHILD_TOKEN_FILE" && -s "$CHILD_TOKEN_FILE" ]] || { echo "$PROG: child token file is required" >&2; exit 2; }
[[ -n "$PROJECT_KEY" ]] || { echo "$PROG: AGENTSTACK_PROJECT_KEY is required" >&2; exit 2; }
[[ -n "${PARENT_AGENT:-}" ]] || { echo "$PROG: PARENT_AGENT is required" >&2; exit 2; }
[[ -n "$RESOURCES" ]] || { echo "$PROG: Gemini dashboard launch requires declared resources" >&2; exit 2; }
[[ -n "$TASK_FILE" && -s "$TASK_FILE" ]] || { echo "$PROG: full task handoff file is missing" >&2; exit 2; }
[[ -d "$WORK_DIR" ]] || { echo "$PROG: workdir does not exist: $WORK_DIR" >&2; exit 2; }
case "$EFFORT" in low|medium|high) ;; *) echo "$PROG: invalid effort: $EFFORT" >&2; exit 2 ;; esac
command -v tmux >/dev/null 2>&1 || { echo "$PROG: tmux not found" >&2; exit 1; }
command -v "$GEMINI_BIN" >/dev/null 2>&1 || { echo "$PROG: Antigravity CLI not found (expected agy)" >&2; exit 1; }
command -v "$PYTHON_BIN" >/dev/null 2>&1 || [[ -x "$PYTHON_BIN" ]] || { echo "$PROG: selected Python is unavailable: $PYTHON_BIN" >&2; exit 1; }
[[ -x "$MAIL_HELPER" && -x "$STREAM_HELPER" && -x "$PROXY_RUNNER" ]] || { echo "$PROG: Gemini provider helpers are not installed" >&2; exit 1; }

SOURCE_REPO="$(git -C "$WORK_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$SOURCE_REPO" ]] || { echo "$PROG: Gemini dashboard launch requires a git repository" >&2; exit 1; }
if [[ -n "$WORKTREE_BASE_REV" ]]; then
  BASE_REV="$(git -C "$SOURCE_REPO" rev-parse --verify "$WORKTREE_BASE_REV^{commit}" 2>/dev/null || true)"
else
  BASE_REV="$(git -C "$SOURCE_REPO" rev-parse --verify HEAD 2>/dev/null || true)"
fi
[[ -n "$BASE_REV" ]] || { echo "$PROG: could not resolve worktree base" >&2; exit 1; }

mkdir -p "$RUNTIME_DIR" "$WORKTREE_ROOT"
chmod 700 "$RUNTIME_DIR" 2>/dev/null || true
TOKEN_KEY="$(printf '%s' "$CHILD_NAME" | LC_ALL=C tr -c 'A-Za-z0-9_.-' '_')"
DURABLE_TOKEN="$RUNTIME_DIR/agent_token_$TOKEN_KEY"
WORKTREE_DIR="$WORKTREE_ROOT/$CHILD_NAME"
BRANCH_NAME="exp/$CHILD_NAME"
MCP_CONFIG=""
TASK_EVENT_FILE="$RUNTIME_DIR/gemini-task-$CHILD_NAME.ndjson"
RUNNER_FILE="$RUNTIME_DIR/gemini-runner-$CHILD_NAME.sh"
RESULT_LOG="$RUNTIME_DIR/gemini-$CHILD_NAME.ndjson"
STDERR_LOG="$RUNTIME_DIR/gemini-$CHILD_NAME.stderr.log"
WORKTREE_CREATED=false
RESERVED=false
TMUX_STARTED=false

mail_helper() {
  AGENTSTACK_HOME="$AGENTSTACK_HOME_DIR" \
  AGENTSTACK_MCP_URL="$MCP_URL" \
  AGENTSTACK_MAIL_ENV="$MAIL_ENV" \
  AGENTSTACK_MAIL_HTTP_BEARER_MODE="$HTTP_BEARER_MODE" \
    "$PYTHON_BIN" "$MAIL_HELPER" "$@"
}

cleanup_failure() {
  status=$?
  if [[ $status -ne 0 ]]; then
    if [[ "$TMUX_STARTED" == true && -n "$CHILD_NAME" ]]; then
      tmux kill-session -t "=$CHILD_NAME" >/dev/null 2>&1 || true
      TMUX_STARTED=false
    fi
    if [[ "$RESERVED" == true && -s "$DURABLE_TOKEN" ]]; then
      mail_helper release --project-key "$PROJECT_KEY" --agent-name "$CHILD_NAME" \
        --token-file "$DURABLE_TOKEN" --paths "$RESOURCES" >/dev/null 2>&1 || true
    fi
    if [[ -s "$DURABLE_TOKEN" ]]; then
      mail_helper retire --project-key "$PROJECT_KEY" --agent-name "$CHILD_NAME" \
        --token-file "$DURABLE_TOKEN" >/dev/null 2>&1 || true
    fi
    if [[ "$WORKTREE_CREATED" == true ]]; then
      git -C "$SOURCE_REPO" worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1 || true
      git -C "$SOURCE_REPO" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
    fi
    rm -f "$TASK_EVENT_FILE" "$RUNNER_FILE" "$MCP_CONFIG" "$DURABLE_TOKEN"
  fi
}
trap cleanup_failure EXIT

# Consume the one-shot token into the stable per-agent runtime path expected by
# the MCP proxy. The token never appears in argv or the generated config.
( umask 077 && cat "$CHILD_TOKEN_FILE" > "$DURABLE_TOKEN" )
chmod 600 "$DURABLE_TOKEN"
rm -f "$CHILD_TOKEN_FILE"

[[ ! -e "$WORKTREE_DIR" ]] || { echo "$PROG: worktree path already exists: $WORKTREE_DIR" >&2; exit 1; }
if git -C "$SOURCE_REPO" show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
  echo "$PROG: child branch already exists: $BRANCH_NAME" >&2
  exit 1
fi
git -C "$SOURCE_REPO" worktree add -b "$BRANCH_NAME" "$WORKTREE_DIR" "$BASE_REV" >/dev/null
WORKTREE_CREATED=true

EXCLUDE_FILE="$(git -C "$WORKTREE_DIR" rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$EXCLUDE_FILE")"
grep -qxF '.agents/mcp_config.json' "$EXCLUDE_FILE" 2>/dev/null || printf '%s\n' '.agents/mcp_config.json' >> "$EXCLUDE_FILE"

mail_helper reserve --project-key "$PROJECT_KEY" --agent-name "$CHILD_NAME" \
  --token-file "$DURABLE_TOKEN" --paths "$RESOURCES" --ttl "$RESOURCE_TTL"
RESERVED=true

if git -C "$WORKTREE_DIR" ls-files --error-unmatch .agents/mcp_config.json >/dev/null 2>&1; then
  echo "$PROG: tracked .agents/mcp_config.json exists; refusing to overwrite it" >&2
  exit 1
fi
mkdir -p "$WORKTREE_DIR/.agents"
MCP_CONFIG="$WORKTREE_DIR/.agents/mcp_config.json"
AGS_GEMINI_MCP_PATH="$MCP_CONFIG" \
AGS_GEMINI_PROXY_RUNNER="$PROXY_RUNNER" \
AGS_GEMINI_CHILD_NAME="$CHILD_NAME" \
AGS_GEMINI_TOKEN_FILE="$DURABLE_TOKEN" \
AGS_GEMINI_PROJECT_KEY="$PROJECT_KEY" \
AGS_GEMINI_MCP_URL="$MCP_URL" \
AGS_GEMINI_MAIL_ENV="$MAIL_ENV" \
AGS_GEMINI_BEARER_MODE="$HTTP_BEARER_MODE" \
AGS_GEMINI_RUNTIME_DIR="$RUNTIME_DIR" \
"$PYTHON_BIN" - <<'PY'
import json, os
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
            os.environ["AGS_GEMINI_RUNTIME_DIR"], "gemini-proxy-" + os.environ["AGS_GEMINI_CHILD_NAME"]),
    },
}
path.write_text(json.dumps({"mcpServers": {"orrery-mail": entry}}, indent=2) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

"$PYTHON_BIN" - "$TASK_FILE" "$TASK_EVENT_FILE" "$CHILD_NAME" "$PARENT_AGENT" "$RESOURCES" <<'PY'
import json, os, sys
raw_path, event_path, child, parent, resources = sys.argv[1:6]
task = open(raw_path, encoding="utf-8").read()
prefix = (
    f"You are {child}, a delegated Antigravity child of {parent}. "
    "Work only in this isolated worktree. The launcher already reserved the declared resources. "
    "Do not register another ORRERY identity. The launcher will report your final result to the parent automatically. "
    f"Declared resources: {resources}.\n\nCanonical task:\n"
)
with open(event_path, "w", encoding="utf-8") as fh:
    json.dump({"event": "user", "message": {"content": prefix + task}}, fh, ensure_ascii=False, separators=(",", ":"))
    fh.write("\n")
os.chmod(event_path, 0o600)
PY
rm -f "$TASK_FILE"

cat > "$RUNNER_FILE" <<EOF
#!/usr/bin/env bash
set -uo pipefail
cd $(printf '%q' "$WORKTREE_DIR") || exit 1
export AGENT_NAME=$(printf '%q' "$CHILD_NAME")
export PARENT_AGENT=$(printf '%q' "$PARENT_AGENT")
export AGENTSTACK_RESERVED_IDENTITY=1
export AGENTSTACK_PROJECT_KEY=$(printf '%q' "$PROJECT_KEY")
export AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR")
export AGENTSTACK_HOOKS_DIR=$(printf '%q' "$HOOKS_DIR")
export AGENTSTACK_RUNTIME_DIR=$(printf '%q' "$RUNTIME_DIR")
export AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL")
export AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV")
export AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE")
export AGENTSTACK_PYTHON=$(printf '%q' "$PYTHON_BIN")
set +e
cat $(printf '%q' "$TASK_EVENT_FILE") | \
  $(printf '%q' "$GEMINI_BIN") --input-format stream-json --output-format stream-json \
    --model $(printf '%q' "$MODEL") --effort $(printf '%q' "$EFFORT") \
    --print-timeout $(printf '%q' "$PRINT_TIMEOUT") \
    2> >(tee $(printf '%q' "$STDERR_LOG") >&2) | \
  $(printf '%q' "$PYTHON_BIN") $(printf '%q' "$STREAM_HELPER") $(printf '%q' "$RESULT_LOG")
agy_status=\${PIPESTATUS[1]}
set -e
AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR") \
AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL") \
AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV") \
AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE") \
  $(printf '%q' "$PYTHON_BIN") $(printf '%q' "$MAIL_HELPER") report --project-key $(printf '%q' "$PROJECT_KEY") \
    --agent-name $(printf '%q' "$CHILD_NAME") --token-file $(printf '%q' "$DURABLE_TOKEN") \
    --parent $(printf '%q' "$PARENT_AGENT") --result-log $(printf '%q' "$RESULT_LOG") \
    --worktree $(printf '%q' "$WORKTREE_DIR") || true
AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR") \
AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL") \
AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV") \
AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE") \
  $(printf '%q' "$PYTHON_BIN") $(printf '%q' "$MAIL_HELPER") release --project-key $(printf '%q' "$PROJECT_KEY") \
    --agent-name $(printf '%q' "$CHILD_NAME") --token-file $(printf '%q' "$DURABLE_TOKEN") \
    --paths $(printf '%q' "$RESOURCES") || true
AGENTSTACK_HOME=$(printf '%q' "$AGENTSTACK_HOME_DIR") \
AGENTSTACK_MCP_URL=$(printf '%q' "$MCP_URL") \
AGENTSTACK_MAIL_ENV=$(printf '%q' "$MAIL_ENV") \
AGENTSTACK_MAIL_HTTP_BEARER_MODE=$(printf '%q' "$HTTP_BEARER_MODE") \
  $(printf '%q' "$PYTHON_BIN") $(printf '%q' "$MAIL_HELPER") retire --project-key $(printf '%q' "$PROJECT_KEY") \
    --agent-name $(printf '%q' "$CHILD_NAME") --token-file $(printf '%q' "$DURABLE_TOKEN") || true
[[ -x $(printf '%q' "$CLEANUP_HELPER") ]] && $(printf '%q' "$CLEANUP_HELPER") || true
rm -f $(printf '%q' "$TASK_EVENT_FILE") $(printf '%q' "$DURABLE_TOKEN") $(printf '%q' "$MCP_CONFIG") $(printf '%q' "$RUNNER_FILE")
echo "[antigravity] child finished; worktree retained at $(printf '%q' "$WORKTREE_DIR")"
exit "\$agy_status"
EOF
chmod 700 "$RUNNER_FILE"

tmux new-session -d -s "$CHILD_NAME" -c "$WORKTREE_DIR" \
  -e "AGENT_NAME=$CHILD_NAME" -e "PARENT_AGENT=$PARENT_AGENT" \
  -e "AGENTSTACK_RESERVED_IDENTITY=1" \
  "/bin/bash $(printf '%q' "$RUNNER_FILE")"
TMUX_STARTED=true

mkdir -p "$(dirname "$MANAGED_FILE")"
grep -qxF "$CHILD_NAME" "$MANAGED_FILE" 2>/dev/null || printf '%s\n' "$CHILD_NAME" >> "$MANAGED_FILE"
trap - EXIT
printf '%s\n' "$CHILD_NAME"
