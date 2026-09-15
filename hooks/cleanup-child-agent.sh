#!/bin/bash
set -euo pipefail

# A SessionEnd hook may run outside the child process and therefore inherit no
# identity. Capture the launcher's claim before the resolver can infer anything:
# cleanup may retire only an agent named explicitly by the caller or its entry
# environment, never one guessed from surrounding pane/session state.
AGENT_NAME_ENV_AT_ENTRY="${AGENT_NAME:-}"
if [[ -z "${1:-}" && -z "$AGENT_NAME_ENV_AT_ENTRY" ]]; then
    exit 0
fi

HOOKS_DIR="${AGENTSTACK_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUNTIME_DIR="${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
PROJECT_CONTEXT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/project-context.sh"
# shellcheck disable=SC1090
. "$PROJECT_CONTEXT_LIB"
STATE_DIR="$RUNTIME_DIR/child-agents"
MANAGED_FILE="${AGENTSTACK_MANAGED_AGENTS_FILE:-$RUNTIME_DIR/managed_agents.txt}"
MCP_URL="${AGENTSTACK_MCP_URL:-${MCP_URL:-http://127.0.0.1:18765/mcp}}"
MAIL_ENV="${AGENTSTACK_MAIL_ENV:-$HOME/.agentstack/mail/.env}"
HTTP_BEARER_MODE="${AGENTSTACK_MAIL_HTTP_BEARER_MODE:-auto}"

get_agentstack_token() {
    if [[ -n "${MCP_AGENT_MAIL_TOKEN:-}" ]]; then
        printf '%s' "$MCP_AGENT_MAIL_TOKEN"
        return 0
    fi
    if [[ -x "$HOOKS_DIR/get-mcp-agent-mail-token.sh" ]]; then
        bash "$HOOKS_DIR/get-mcp-agent-mail-token.sh" 2>/dev/null && return 0
    fi
    if command -v security >/dev/null 2>&1; then
        local keychain_token
        keychain_token=$(security find-generic-password -s "mcp-agent-mail" -a "HTTP_BEARER_TOKEN" -w 2>/dev/null || true)
        if [[ -n "$keychain_token" ]]; then
            printf '%s' "$keychain_token"
            return 0
        fi
    fi
    if [[ -f "$MAIL_ENV" ]]; then
        sed -n 's/^HTTP_BEARER_TOKEN=//p' "$MAIL_ENV" | tr -d '[:space:]'
        return 0
    fi
    return 1
}

legacy_http_bearer_enabled() {
    case "$HTTP_BEARER_MODE" in
        enabled|auto) return 0 ;;
        disabled) return 1 ;;
        *) return 2 ;;
    esac
}

AGENT_NAME="${1:-$AGENT_NAME_ENV_AT_ENTRY}"

if [[ -z "$AGENT_NAME" ]]; then
    exit 0
fi
if [[ ! "$AGENT_NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    # State/config paths below are keyed by the exact register_agent read-back.
    # Reject path separators instead of normalizing an API identity.
    exit 0
fi

STATE_FILE="$STATE_DIR/${AGENT_NAME}.json"
TOKEN_KEY="$(printf '%s' "$AGENT_NAME" | LC_ALL=C tr -c 'A-Za-z0-9_.-' '_')"
TOKEN_FILE="$RUNTIME_DIR/agent_token_$TOKEN_KEY"
MCP_CONFIG_FILE="$STATE_DIR/${AGENT_NAME}.mcp.json"
CODEX_HOME_DIR="$STATE_DIR/${AGENT_NAME}.codex-home"
OWNER_FILE="$RUNTIME_DIR/agent_owner_$TOKEN_KEY.json"
if [[ ! -e "$TOKEN_FILE" && ! -L "$TOKEN_FILE" && ! -e "$STATE_FILE" && ! -L "$STATE_FILE" \
      && ! -e "$OWNER_FILE" && ! -L "$OWNER_FILE" && -z "${CHILD_REGISTRATION_TOKEN:-}" ]]; then
    exit 0
fi

# Caller identity, actual workspace and private credential must agree before any
# Mail request or local deletion. A copied project key is not cleanup authority.
REGISTER_LIB="${AGENTSTACK_REGISTER_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin/lib" && pwd)/agentstack-register.sh}"
[[ -f "$REGISTER_LIB" ]] || { echo "[cleanup-child-agent] missing registration library" >&2; exit 1; }
# shellcheck disable=SC1090
. "$REGISTER_LIB"
WORK_DIR="$(pwd -P)" || exit 1
if ! ags_apply_owned_workspace "$AGENT_NAME" "$WORK_DIR" "" "${CHILD_REGISTRATION_TOKEN:-}"; then
    echo "[cleanup-child-agent] could not read child state or verify workspace ownership for '$AGENT_NAME'; refusing partial cleanup" >&2
    exit 1
fi
CLEANUP_OWNER_TOKEN="$AGS_OWNED_REGISTRATION_TOKEN"
unset AGS_OWNED_REGISTRATION_TOKEN
CLEANUP_PROJECT_KEY="$AGENTSTACK_PROJECT_KEY"

if legacy_http_bearer_enabled; then
    TOKEN=$(get_agentstack_token 2>/dev/null || true)
    bearer_status=0
else
    bearer_status=$?
    TOKEN=""
fi
if [[ "$bearer_status" == "2" ]]; then
    exit 0
fi
if [[ "$bearer_status" == "0" && -z "$TOKEN" ]]; then
    exit 0
fi

call_mcp() {
    local method="$1"
    local args_json="$2"
    printf '%s\0%s' "$args_json" "$TOKEN" | python3 -c '
import json
import sys
import http.client
from urllib.parse import urlparse

method = sys.argv[1]
url = sys.argv[2]
args_raw, token = sys.stdin.buffer.read().split(b"\0", 1)
args = json.loads(args_raw)
token = token.decode("utf-8")

parsed = urlparse(url)
payload = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": method, "arguments": args},
}).encode()

conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Connection": "close",
}
if token:
    headers["Authorization"] = f"Bearer {token}"
conn.request("POST", parsed.path, body=payload, headers=headers)
resp = conn.getresponse()
print(resp.read().decode())
conn.close()
' "$method" "$MCP_URL"
}

cleanup_owner_still_matches() {
    local context
    context="$(ags_registration_owner_context "$AGENT_NAME" "$CLEANUP_OWNER_TOKEN" "$WORK_DIR")" || return 1
    ags_project_keys_equal "$(ags_registration_context_project_key "$context")" "$CLEANUP_PROJECT_KEY"
}

release_args=$(python3 -c "
import json, sys
print(json.dumps({
    'project_key': sys.argv[1],
    'agent_name': sys.argv[2],
}))
" "$PROJECT_KEY" "$AGENT_NAME")
call_mcp "release_file_reservations" "$release_args" > /dev/null 2>&1 || true

# Recheck after the release request before using the frozen retire credential.
cleanup_owner_still_matches || exit 1
retire_args=$(CLEANUP_REGISTRATION_TOKEN="$CLEANUP_OWNER_TOKEN" python3 -c '
import json
import os
import sys
print(json.dumps({
    "project_key": sys.argv[1],
    "agent_name": sys.argv[2],
    "registration_token": os.environ["CLEANUP_REGISTRATION_TOKEN"],
}))
' "$CLEANUP_PROJECT_KEY" "$AGENT_NAME") || retire_args=""
if [[ -n "$retire_args" ]]; then
    call_mcp "retire_agent" "$retire_args" > /dev/null 2>&1 || true
fi

cleanup_owner_still_matches || exit 1

python3 - "$MANAGED_FILE" "$AGENT_NAME" <<'PYEOF' 2>/dev/null || true
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
name = sys.argv[2]
try:
    lines = path.read_text(encoding="utf-8").splitlines()
except OSError:
    raise SystemExit(0)
path.write_text("\n".join(line for line in lines if line != name) + "\n", encoding="utf-8")
PYEOF
rm -f "$STATE_FILE" "$TOKEN_FILE" "$MCP_CONFIG_FILE"
rm -rf "$CODEX_HOME_DIR"
# Release the durable name only after its owned runtime artifacts are gone.
rm -f "$OWNER_FILE"

exit 0
