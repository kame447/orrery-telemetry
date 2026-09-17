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

# Only the caller's explicit claim names the child; nothing is inferred from
# the surrounding pane or session.
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
if [[ ! -e "$TOKEN_FILE" && ! -L "$TOKEN_FILE" && ! -e "$STATE_FILE" && ! -L "$STATE_FILE" \
      && -z "${CHILD_REGISTRATION_TOKEN:-}" ]]; then
    exit 0
fi

REGISTER_LIB="${AGENTSTACK_REGISTER_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bin/lib/agentstack-register.sh}"
if [[ ! -f "$REGISTER_LIB" ]]; then
    echo "[cleanup-child-agent] registration library is unavailable; leaving child state intact" >&2
    exit 1
fi
# shellcheck disable=SC1090
. "$REGISTER_LIB"

# One snapshot of the child's private files. The token and project come only
# from them (or, without state, from an explicit token and the live key); every
# copy that exists must agree. The fingerprint lets later steps notice a
# replacement written while Mail was being asked.
read_child_snapshot() {
    CHILD_REGISTRATION_TOKEN="${CHILD_REGISTRATION_TOKEN:-}" CLEANUP_AGENT_NAME="$AGENT_NAME" \
        python3 - "$TOKEN_FILE" "$STATE_FILE" <<'PYEOF'
import hashlib
import hmac
import json
import os
import stat
import sys

token_file, state_file = sys.argv[1:3]


def read_private(path):
    if not os.path.lexists(path):
        return None
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("child credential must be a private regular file")
        return os.read(descriptor, 65537).decode("utf-8")
    finally:
        os.close(descriptor)


token_text = read_private(token_file)
state_text = read_private(state_file)
state = json.loads(state_text) if state_text is not None else None
if state is not None and not isinstance(state, dict):
    raise ValueError("child state must be an object")
if state is not None and state.get("agent_name") not in (None, os.environ["CLEANUP_AGENT_NAME"]):
    raise ValueError("child state belongs to another agent")
tokens = []
if token_text is not None:
    tokens.append(token_text.strip())
if state is not None:
    tokens.append(state.get("registration_token") or "")
if os.environ.get("CHILD_REGISTRATION_TOKEN"):
    tokens.append(os.environ["CHILD_REGISTRATION_TOKEN"])
if not tokens or not all(isinstance(t, str) and t for t in tokens):
    raise ValueError("child credential is empty")
if not all(hmac.compare_digest(t, tokens[0]) for t in tokens):
    raise ValueError("child credentials disagree")
project = (state or {}).get("project_key") or ""
repository = (state or {}).get("repository_key") or ""
work_dir = (state or {}).get("work_dir") or ""
fingerprint = hashlib.sha256(
    ((token_text or "-") + "\0" + (state_text or "-")).encode("utf-8")).hexdigest()
for value in (fingerprint, project, repository, work_dir):
    if not isinstance(value, str) or "\n" in value:
        raise ValueError("child state field is invalid")
    print(value)
PYEOF
}

if ! SNAPSHOT="$(read_child_snapshot 2>/dev/null)"; then
    echo "[cleanup-child-agent] could not read child state for '$AGENT_NAME'; refusing partial cleanup" >&2
    exit 1
fi
SNAPSHOT_FINGERPRINT="$(printf '%s\n' "$SNAPSHOT" | sed -n '1p')"
STATE_PROJECT_KEY="$(printf '%s\n' "$SNAPSHOT" | sed -n '2p')"
STATE_REPOSITORY="$(printf '%s\n' "$SNAPSHOT" | sed -n '3p')"
STATE_WORK_DIR="$(printf '%s\n' "$SNAPSHOT" | sed -n '4p')"

snapshot_unchanged() {
    local current
    current="$(read_child_snapshot 2>/dev/null)" || return 1
    [[ "$(printf '%s\n' "$current" | sed -n '1p')" == "$SNAPSHOT_FINGERPRINT" ]]
}

# The project is the child's recorded one; a live key is used only when no
# state exists, and either way it must validate for this actual workspace.
PROJECT_KEY="${STATE_PROJECT_KEY:-${AGENTSTACK_PROJECT_KEY:-${PROJECT_KEY:-}}}"
if [[ -z "$PROJECT_KEY" ]]; then
    echo "[cleanup-child-agent] project key is unavailable for '$AGENT_NAME'; leaving child state intact" >&2
    exit 1
fi
refuse_workspace() {
    echo "[cleanup-child-agent] $1; leaving child state intact" >&2
    exit 1
}
# A recorded workspace is evidence only if the recorded project, repository
# and directory still form one valid tuple. A contradictory record never
# authorizes cleanup, whatever the current directory is.
SAVED_PROJECT_KEY=""
SAVED_WORK_DIR=""
if [[ -n "$STATE_WORK_DIR" ]]; then
    ags_child_target_context "$STATE_WORK_DIR" "$PROJECT_KEY" \
        || refuse_workspace "the recorded workspace of '$AGENT_NAME' does not belong to '$PROJECT_KEY'"
    [[ "$AGS_CHILD_REPOSITORY" == "$STATE_REPOSITORY" ]] \
        || refuse_workspace "the recorded repository of '$AGENT_NAME' contradicts its recorded workspace"
    SAVED_PROJECT_KEY="$AGS_CHILD_PROJECT_KEY"
    SAVED_WORK_DIR="$AGS_CHILD_WORK_DIR"
fi
ags_child_target_context "$(pwd -P)" "$PROJECT_KEY" \
    || refuse_workspace "this workspace does not belong to '$AGENT_NAME' in '$PROJECT_KEY'"
if [[ -n "$SAVED_PROJECT_KEY" && "$AGS_CHILD_PROJECT_KEY" != "$SAVED_PROJECT_KEY" ]]; then
    refuse_workspace "this workspace resolves to another namespace than '$AGENT_NAME' was started in"
fi
if [[ -n "$STATE_REPOSITORY" ]]; then
    [[ "$AGS_CHILD_REPOSITORY" == "$STATE_REPOSITORY" ]] \
        || refuse_workspace "this repository is not the one '$AGENT_NAME' was started in"
elif [[ -n "$SAVED_WORK_DIR" ]]; then
    case "$AGS_CHILD_WORK_DIR/" in
        "$SAVED_WORK_DIR"/*) ;;
        *) refuse_workspace "this directory is outside the workspace '$AGENT_NAME' was started in" ;;
    esac
fi
CLEANUP_PROJECT_KEY="$AGS_CHILD_PROJECT_KEY"

if legacy_http_bearer_enabled; then
    TOKEN=$(get_agentstack_token 2>/dev/null || true)
    bearer_status=0
else
    bearer_status=$?
    TOKEN=""
fi
if [[ "$bearer_status" == "2" ]]; then
    exit 1
fi
if [[ "$bearer_status" == "0" && -z "$TOKEN" ]]; then
    echo "[cleanup-child-agent] ORRERY Mail bearer is unavailable; leaving child state intact" >&2
    exit 1
fi

# Mail must accept this exact credential for this name in this project before
# anything is released, retired, or deleted.
verify_child_ownership() {
    local source
    if [[ -e "$TOKEN_FILE" || -L "$TOKEN_FILE" ]]; then
        source="$TOKEN_FILE"
    elif [[ -e "$STATE_FILE" || -L "$STATE_FILE" ]]; then
        source="state:$STATE_FILE"
    else
        AGENTSTACK_MCP_URL="$MCP_URL" MCP_AGENT_MAIL_TOKEN="$TOKEN" \
            ags_verify_registration_token "$CLEANUP_PROJECT_KEY" "$AGENT_NAME" "$CHILD_REGISTRATION_TOKEN"
        return
    fi
    AGENTSTACK_MCP_URL="$MCP_URL" MCP_AGENT_MAIL_TOKEN="$TOKEN" \
        ags_verify_child_credential "$CLEANUP_PROJECT_KEY" "$AGENT_NAME" "$source"
}
if ! verify_child_ownership; then
    echo "[cleanup-child-agent] ORRERY Mail did not confirm '$AGENT_NAME' in '$CLEANUP_PROJECT_KEY' with this credential; leaving child state intact" >&2
    exit 1
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
body = resp.read().decode()
conn.close()
print(body)
if not 200 <= resp.status < 300:
    raise SystemExit(1)
' "$method" "$MCP_URL"
}

release_args=$(python3 -c "
import json, sys
print(json.dumps({
    'project_key': sys.argv[1],
    'agent_name': sys.argv[2],
}))
" "$CLEANUP_PROJECT_KEY" "$AGENT_NAME")
call_mcp "release_file_reservations" "$release_args" > /dev/null 2>&1 || true

# A replacement written during the release belongs to a newer registration.
if ! snapshot_unchanged; then
    echo "[cleanup-child-agent] credentials for '$AGENT_NAME' changed during cleanup; leaving them intact" >&2
    exit 1
fi
retire_args=$(CHILD_REGISTRATION_TOKEN="${CHILD_REGISTRATION_TOKEN:-}" python3 -c '
import json
import os
import pathlib
import sys

project_key, agent_name, token_file, state_file = sys.argv[1:5]
token = ""
if pathlib.Path(token_file).is_file():
    token = pathlib.Path(token_file).read_text(encoding="utf-8").strip()
elif pathlib.Path(state_file).is_file():
    token = json.loads(
        pathlib.Path(state_file).read_text(encoding="utf-8")
    ).get("registration_token", "")
else:
    token = os.environ.get("CHILD_REGISTRATION_TOKEN", "")
if not token:
    raise SystemExit(1)
print(json.dumps({
    "project_key": project_key,
    "agent_name": agent_name,
    "registration_token": token,
}))
' "$CLEANUP_PROJECT_KEY" "$AGENT_NAME" "$TOKEN_FILE" "$STATE_FILE") || retire_args=""
# The durable credential is the only way to retire this identity later, so it
# is kept unless Mail returns its positive retirement receipt for this agent
# and project (transport failure, HTTP error, JSON-RPC/tool error, or an empty
# or mismatched answer all count as unconfirmed).
retire_response=""
if [[ -n "$retire_args" ]]; then
    retire_response="$(call_mcp "retire_agent" "$retire_args" 2>/dev/null)" || retire_response=""
fi
if [[ -z "$retire_response" ]] \
    || ! printf '%s' "$retire_response" | ags_retire_receipt_confirms "$AGENT_NAME" "$CLEANUP_PROJECT_KEY"; then
    echo "[cleanup-child-agent] ORRERY Mail did not confirm retiring '$AGENT_NAME'; keeping its credentials for a later cleanup" >&2
    exit 1
fi

if ! snapshot_unchanged; then
    echo "[cleanup-child-agent] credentials for '$AGENT_NAME' changed during cleanup; leaving them intact" >&2
    exit 1
fi

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

exit 0
