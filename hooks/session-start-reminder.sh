#!/bin/bash
# session-start-reminder.sh
# SessionStart hook for startup/resume/clear/compact.
#
# If an existing identity can be resolved, print a reminder to re-register with
# the same ORRERY Mail name instead of generating a fresh identity.

HOOKS_DIR="${AGENTSTACK_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUNTIME_DIR="${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
PROJECT_CONTEXT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/project-context.sh"
# shellcheck disable=SC1090
. "$PROJECT_CONTEXT_LIB"
MCP_URL="${AGENTSTACK_MCP_URL:-${MCP_URL:-http://127.0.0.1:18765/mcp}}"
HEALTH_URL="${AGENTSTACK_MCP_HEALTH_URL:-${MCP_AGENT_MAIL_HEALTH_URL:-}}"
PROJECT_KEY=""
SESSION_CONTEXT_JSON=""
RESOLVED_AGENT=""
RESOLVED_AGENT_SRC="none"
SHELL_REGISTERED_AGENT=""
SHELL_REGISTRATION_ERROR=""
SHELL_OWNERSHIP_PREPARED=0
SESSION_IDENTITY_CONFLICT=0

if [ -z "$HEALTH_URL" ]; then
    case "$MCP_URL" in
        */mcp) HEALTH_URL="${MCP_URL%/mcp}/health/liveness" ;;
        *) HEALTH_URL="${MCP_URL%/}/health/liveness" ;;
    esac
fi

# Is the mail server answering?
#
# The obvious probe -- GET the liveness URL derived above -- was wrong in a way
# that looked exactly like the server being down. ORRERY Mail serves its MCP
# path and the configured aliases and nothing else, so there is no
# /health/liveness route to answer, and `curl -sf` fails on any non-2xx. Every
# healthy install therefore reported "not running" at every session start, every
# session was told to skip registration, and no agent ever refreshed
# last_active_ts. That timestamp is what keeps the staleness sweep off an
# agent's file reservations, so a probe that could never succeed ended in
# reservations being collected out from under agents that were working.
#
# Ask the server what it is, not merely whether something answers. A bare GET
# returning 405/406 only says "some HTTP server is here"; any service that
# rejects GET would pass that. Call health_check over MCP and require the
# structured status, so a different service on the same port is not mistaken
# for this one. The liveness URL is still honoured when it answers, so a
# deployment that fronts the service with a real health route keeps working.
mail_server_is_answering() {
    if [ -n "${AGENTSTACK_MCP_HEALTH_URL:-}${MCP_AGENT_MAIL_HEALTH_URL:-}" ] &&
        curl -sf -m 2 "$HEALTH_URL" >/dev/null 2>&1; then
        # Only an explicitly configured health URL is trusted on status alone;
        # the derived one is a guess, and "some 200" is not this service.
        return 0
    fi
    # Cap what a session start will read. This runs on every session, and an
    # unbounded body is free memory amplification for anything on that port.
    response="$(curl -s -m 3 --max-filesize 262144 -X POST "$MCP_URL" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -w '\n__HTTP_STATUS__%{http_code}' \
        -d '{"jsonrpc":"2.0","id":"session-start","method":"tools/call","params":{"name":"health_check","arguments":{}}}' \
        2>/dev/null)" || return 1
    printf '%s' "$response" | python3 -c '
import json, sys

raw = sys.stdin.read(262144 + 64)
marker = "\n__HTTP_STATUS__"
if marker not in raw:
    raise SystemExit(1)
body, _, status = raw.rpartition(marker)
if not status.strip().isdigit() or not 200 <= int(status.strip()) < 300:
    raise SystemExit(1)

def documents(text):
    """The endpoint may answer as JSON or as an SSE stream.

    An SSE event carries its payload across as many ``data:`` lines as it
    likes, joined by newlines -- pretty-printed JSON arrives that way. Reading
    each line as its own document rejects a perfectly valid reply.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        yield stripped
    payload = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload.append(line[5:].lstrip())
        elif not line.strip():
            if payload:
                yield "\n".join(payload)
                payload = []
    if payload:
        yield "\n".join(payload)

for document in documents(body):
    try:
        message = json.loads(document)
    except ValueError:
        continue
    if not isinstance(message, dict):
        continue
    # A JSON-RPC reply to the call we made, carrying a healthy status. Anything
    # less -- an error object that happens to contain "ok", a bare status
    # document, someone else true JSON -- is not this server saying it is well.
    if message.get("jsonrpc") != "2.0" or message.get("id") != "session-start":
        continue
    if message.get("error") is not None:
        continue
    result = message.get("result")
    if not isinstance(result, dict):
        continue
    health = result.get("structuredContent")
    if not isinstance(health, dict):
        continue
    if health.get("status") == "ok":
        raise SystemExit(0)
raise SystemExit(1)
' >/dev/null 2>&1
}

# The SessionStart payload carries the session id, which is how a client with
# no launcher is identified: resolve-agent-name.sh looks up the binding that
# register_agent recorded for it. Without this the reminder cannot name an
# identity that already exists, and tells an established session it is nobody.
if [ ! -t 0 ]; then
    SESSION_START_INPUT="$(cat)"
    AGENTSTACK_SESSION_ID="$(printf '%s' "$SESSION_START_INPUT" | python3 -c '
import json, sys
try:
    value = json.loads(sys.stdin.read(262144)).get("session_id", "")
    print(value if isinstance(value, str) else "")
except Exception:
    print("")
' 2>/dev/null || echo "")"
    export AGENTSTACK_SESSION_ID
fi

HOOK_CWD="$(printf '%s' "${SESSION_START_INPUT:-}" | python3 -c '
import json, sys
try:
    value = json.loads(sys.stdin.read(262144) or "{}").get("cwd", "")
except Exception:
    value = ""
print(value if isinstance(value, str) else "")
' 2>/dev/null || echo "")"
[ -n "$HOOK_CWD" ] || HOOK_CWD="$(pwd -P)"
BASE_CONTEXT_JSON="$(agentstack_resolve_invocation_context "$HOOK_CWD" 2>/dev/null || true)"
if [ -n "$BASE_CONTEXT_JSON" ]; then
    PROJECT_KEY="$(agentstack_context_field "$BASE_CONTEXT_JSON" project_key 2>/dev/null || true)"
    AGENTSTACK_LOOKUP_PROJECT_KEY="$PROJECT_KEY"
    AGENTSTACK_LOOKUP_REPOSITORY_KEY="$(agentstack_context_field "$BASE_CONTEXT_JSON" repository_key 2>/dev/null || true)"
    AGENTSTACK_LOOKUP_WORK_DIR="$(agentstack_context_field "$BASE_CONTEXT_JSON" work_dir 2>/dev/null || true)"
    export AGENTSTACK_LOOKUP_PROJECT_KEY AGENTSTACK_LOOKUP_REPOSITORY_KEY AGENTSTACK_LOOKUP_WORK_DIR
fi

if [ -f "$HOOKS_DIR/resolve-agent-name.sh" ]; then
    # shellcheck disable=SC1091
    source "$HOOKS_DIR/resolve-agent-name.sh"
fi
if [ "$RESOLVED_AGENT_SRC" = "identity-conflict" ]; then
    SESSION_IDENTITY_CONFLICT=1
elif [ -n "${AGENTSTACK_SESSION_ID:-}" ] && \
     command -v agentstack_session_binding_conflict >/dev/null 2>&1 && \
     [ "$(agentstack_session_binding_conflict "$AGENTSTACK_SESSION_ID" "$AGENTSTACK_LOOKUP_PROJECT_KEY" \
        "${AGENT_NAME:-}" "$AGENTSTACK_LOOKUP_REPOSITORY_KEY" "$AGENTSTACK_LOOKUP_WORK_DIR")" = "conflict" ]; then
    SESSION_IDENTITY_CONFLICT=1
fi

find_register_lib() {
    local candidate
    if [ -n "${AGENTSTACK_REGISTER_LIB:-}" ] && [ -f "$AGENTSTACK_REGISTER_LIB" ]; then
        printf '%s\n' "$AGENTSTACK_REGISTER_LIB"
        return 0
    fi
    for candidate in \
        "${AGENTSTACK_HOME:-$HOME/.agentstack}/bin/lib/agentstack-register.sh" \
        "$HOOKS_DIR/../bin/lib/agentstack-register.sh" \
        "$HOME/.agentstack/bin/lib/agentstack-register.sh"; do
        if [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# True when this session's ORRERY Mail MCP server is the per-child proxy that
# spawn_child.sh configured. The proxy injects the token, so the agent must not
# read it: doing so triggers a Bash approval prompt and pulls the secret into
# the model's context for no benefit.
child_uses_mcp_proxy() {
    agent_name="$1"
    [ -n "$agent_name" ] || return 1
    # Claude children get --mcp-config; Codex children get their own CODEX_HOME.
    [ -n "${CLAUDE_CHILD_MCP_CONFIG:-}" ] && return 0
    [ -f "$RUNTIME_DIR/child-agents/${agent_name}.mcp.json" ] && return 0
    [ -f "$RUNTIME_DIR/child-agents/${agent_name}.codex-home/config.toml" ] && return 0
    return 1
}

prepare_resolved_agent_ownership() {
    local register_lib restored_token work_dir resolved_context
    [ -n "$RESOLVED_AGENT" ] || return 1
    register_lib="$(find_register_lib)" || return 1
    # shellcheck disable=SC1090
    . "$register_lib" || return 1

    restored_token="${CHILD_REGISTRATION_TOKEN:-}"
    if [ -z "$restored_token" ]; then
        restored_token="$(ags_load_registration_token "$RESOLVED_AGENT" 2>/dev/null || true)"
    fi
    [ -n "$restored_token" ] || return 1

    CHILD_REGISTRATION_TOKEN="$restored_token"
    export CHILD_REGISTRATION_TOKEN
    work_dir="$HOOK_CWD"
    resolved_context="$(ags_resolve_registration_context "" "$work_dir" \
        "$RESOLVED_AGENT" reserved "$restored_token")" || {
        SHELL_REGISTRATION_ERROR="persisted ownership for '$RESOLVED_AGENT' does not match workspace '$work_dir'; relaunch with an explicit project namespace"
        return 1
    }
    PROJECT_KEY="$(ags_registration_context_project_key "$resolved_context")" || return 1
    SESSION_CONTEXT_JSON="$resolved_context"
    agentstack_export_context_json "$SESSION_CONTEXT_JSON" || return 1
    SHELL_OWNERSHIP_PREPARED=1
    return 0
}

shell_register_resolved_agent() {
    local model work_dir
    [ "$SHELL_OWNERSHIP_PREPARED" = "1" ] || return 1
    [ -n "$RESOLVED_AGENT" ] || return 1
    [ -n "$PROJECT_KEY" ] || return 1
    work_dir="$HOOK_CWD"
    ags_mail_load_token
    # spawn_child.sh hands the child its model as CLAUDE_CHILD_MODEL; without
    # it this re-registration overwrote the pre-registered model with the
    # program name, and the dashboard lost the provider (no logo, chip said
    # "CLAUDE-CODE" — seen on WSL2, where no pane model is parsed either).
    model="${AGENTSTACK_CLAUDE_MODEL:-${CLAUDE_CHILD_MODEL:-claude-code}}"
    ags_register_session "$PROJECT_KEY" "claude-code" "$model" "cc" "$work_dir" "$RESOLVED_AGENT" "reserved" "" "session-start" >/dev/null 2>&1
    register_status=$?
    if [ "$register_status" -ne 0 ]; then
        if [ "$register_status" -eq 2 ] && [ "${AGS_AGENT_NAME_SUBSTITUTED:-0}" = "1" ]; then
            SHELL_REGISTRATION_ERROR="ORRERY Mail changed reserved identity '$RESOLVED_AGENT' to '${AGS_SERVER_RETURNED_AGENT_NAME:-unknown}'"
        fi
        return 1
    fi
    SHELL_REGISTERED_AGENT="${AGS_REGISTERED_AGENT_NAME:-$RESOLVED_AGENT}"
    record_shell_registration_index
    return 0
}

# A session registered here never calls register_agent itself, so the
# PostToolUse writer (mark-agent-registered.sh) never runs for it and no
# session index is written. Every pre-registered child is such a session, and
# without the index the dashboard falls back to guessing its transcript by
# name counts -- a parent's transcript mentions the child at least as often as
# the child's own, so "resume" opened the wrong history or none (WSL2 test,
# 2026-09-07). Write the same record from the same SessionStart payload.
record_shell_registration_index() {
    [ -n "${AGENTSTACK_SESSION_ID:-}" ] || return 0
    [ -n "${AGS_REGISTERED_AGENT_ID:-}" ] || return 0
    [ -f "$HOOKS_DIR/record-session-index.py" ] || return 0
    AGS_START_INPUT="${SESSION_START_INPUT:-}" python3 -c '
import json, os, sys
try:
    start = json.loads(os.environ.get("AGS_START_INPUT") or "{}")
except Exception:
    start = {}
agent_id, name, project_key, work_dir = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
print(json.dumps({
    "session_id": start.get("session_id", ""),
    "transcript_path": start.get("transcript_path", ""),
    "cwd": work_dir,
    "tool_response": {"id": agent_id, "name": name},
    "tool_input": {"project_key": project_key, "name": name},
}))
' "$AGS_REGISTERED_AGENT_ID" "$SHELL_REGISTERED_AGENT" "$PROJECT_KEY" \
    "$(agentstack_context_field "$SESSION_CONTEXT_JSON" work_dir)" 2>/dev/null |
    AGENTSTACK_REGISTERING_SOURCE="${RESOLVED_AGENT_SRC:-env}" \
    AGENTSTACK_REGISTERING_AGENT="$SHELL_REGISTERED_AGENT" \
    AGENTSTACK_VALIDATED_CONTEXT_JSON="$SESSION_CONTEXT_JSON" \
    AGENTSTACK_RUNTIME_DIR="$RUNTIME_DIR" \
        python3 "$HOOKS_DIR/record-session-index.py" >/dev/null 2>&1 || true
}

mkdir -p "$RUNTIME_DIR" 2>/dev/null
if [ -n "${TMUX_PANE:-}" ]; then
    CURRENT_SESSION=$(tmux display-message -t "$TMUX_PANE" -p '#S' 2>/dev/null)
else
    CURRENT_SESSION=$(tmux display-message -p '#S' 2>/dev/null)
fi
printf '%s src=%s resolved=%q AGENT_NAME=%q TMUX_PANE=%q TMUX=%s sess=%q\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S')" \
    "$RESOLVED_AGENT_SRC" "${RESOLVED_AGENT:-}" "${AGENT_NAME:-}" "${TMUX_PANE:-}" \
    "$([ -n "${TMUX:-}" ] && echo yes || echo no)" \
    "${CURRENT_SESSION:-}" \
    >> "$RUNTIME_DIR/session-start-resolve.log" 2>/dev/null

if [ "$SESSION_IDENTITY_CONFLICT" = "1" ]; then
    echo "ORRERY Mail registration was not attempted because more than one identity claims this session."
    echo "ERROR: resolve the session-index/launcher identity conflict before resuming; do not generate or adopt another name."
elif [ -n "$RESOLVED_AGENT" ] && ! prepare_resolved_agent_ownership; then
    echo "ORRERY Mail registration was not attempted because this identity could not be validated locally."
    if [ -n "$SHELL_REGISTRATION_ERROR" ]; then
        echo "ERROR: $SHELL_REGISTRATION_ERROR。identity split を避けるため停止しました。別名を生成・採用せず、この不一致を operator に報告してください。"
    else
        echo "ERROR: persisted token/ownership for '${RESOLVED_AGENT}' is unavailable or unsafe. 別名を生成・採用せず、この identity の復旧を operator に依頼してください。"
    fi
elif mail_server_is_answering; then
    if [ -n "$RESOLVED_AGENT" ] && shell_register_resolved_agent; then
        echo "ORRERY Mail server is running. This session is already registered."
        echo "あなたは「${SHELL_REGISTERED_AGENT}」です（既存 identity・source: ${RESOLVED_AGENT_SRC}）。shell hook で登録済みです。"
        echo "新しい名前を生成せず、register_agent を呼び直さず、fetch_inbox から始めてください。"
        echo "1. fetch_inbox (agent_name=\"$SHELL_REGISTERED_AGENT\")"
        if child_uses_mcp_proxy "$SHELL_REGISTERED_AGENT"; then
            # The local proxy holds this agent's token and authenticates every
            # call. Telling the agent to read the token anyway costs a Bash
            # approval prompt for nothing, and puts the secret in its context.
            echo "この接続はローカル MCP proxy 経由で既に認証済みです。token ファイルを読む必要はありません（読まないでください）。"
        else
            echo "初回の fetch_inbox/whois では、$RUNTIME_DIR/agent_token_${SHELL_REGISTERED_AGENT} を読み、registration_token に渡してください。"
        fi
    elif [ -n "$RESOLVED_AGENT" ]; then
        echo "ORRERY Mail server is running. Register this session before working."
        if [ -n "$SHELL_REGISTRATION_ERROR" ]; then
            echo "ERROR: $SHELL_REGISTRATION_ERROR。identity split を避けるため停止しました。別名を生成・採用せず、この不一致を operator に報告してください。"
        else
            echo "あなたの名前は「${RESOLVED_AGENT}」です（既存 identity・source: ${RESOLVED_AGENT_SRC}）。新しい名前を生成せず、必ず name=\"${RESOLVED_AGENT}\" で register_agent してください。"
            echo "1. ensure_project -> 2. register_agent (name=\"$RESOLVED_AGENT\") -> 3. fetch_inbox"
        fi
    else
        echo "ORRERY Mail server is running. Register this session before working."
        echo "1. ensure_project -> 2. register_agent (new AdjectiveScientist name if needed) -> 3. fetch_inbox"
    fi
else
    echo "ORRERY Mail server is not running; skip registration until it is available."
    if [ -n "$RESOLVED_AGENT" ]; then
        # Say who this session already is. Otherwise a resumed session waits out
        # the outage believing it is nobody, and registers a second identity for
        # itself as soon as the service returns.
        echo "あなたは既に「${RESOLVED_AGENT}」です（source: ${RESOLVED_AGENT_SRC}）。復旧後も新しい名前を生成せず、この名前で登録し直してください。"
    fi
    echo "この間、ファイル予約は取得も確認もできません。他のエージェントと同じファイルを編集しても衝突は検出されません。"
fi
