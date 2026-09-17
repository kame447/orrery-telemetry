#!/bin/bash
# Shared identity, path, project, endpoint, and authentication rules for the
# file-reservation hooks. Keep this file compatible with macOS /bin/bash 3.2.

HOOKS_DIR="${AGENTSTACK_HOOKS_DIR:-${HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}}"
RUNTIME_DIR="${AGENTSTACK_RUNTIME_DIR:-${RUNTIME_DIR:-$HOME/.agentstack/runtime}}"
PROJECT_CONTEXT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/project-context.sh"
# shellcheck disable=SC1090
. "$PROJECT_CONTEXT_LIB"
# Project, workspace and protected roots are resolved per hook invocation by
# reservation_resolve_workspace below, never from this process's own directory.
RESERVATION_PROJECT_KEY=""
RESERVATION_WORK_DIR=""
PROTECTED_ROOTS=""

POLICY_LIB_EARLY="$HOOKS_DIR/session-identity-policy.sh"
if [ -f "$POLICY_LIB_EARLY" ]; then
    # shellcheck disable=SC1090
    . "$POLICY_LIB_EARLY"
    # Empty is an answer when the installed policy has no endpoint. Filling it
    # with a legacy default could make sibling hooks talk to different servers.
    MCP_URL="$(agentstack_mail_endpoint)"
else
    MCP_URL="${AGENTSTACK_MCP_URL:-${MCP_URL:-http://127.0.0.1:18765/api/}}"
fi

if command -v agentstack_installed_env_value >/dev/null 2>&1; then
    RESERVATION_INSTALLED_MAIL_ENV="$(agentstack_installed_env_value AGENTSTACK_MAIL_ENV 2>/dev/null)"
    RESERVATION_INSTALLED_BEARER_MODE="$(agentstack_installed_env_value AGENTSTACK_MAIL_HTTP_BEARER_MODE 2>/dev/null)"
else
    RESERVATION_INSTALLED_MAIL_ENV=""
    RESERVATION_INSTALLED_BEARER_MODE=""
fi
MAIL_ENV="${AGENTSTACK_MAIL_ENV:-$RESERVATION_INSTALLED_MAIL_ENV}"
MAIL_ENV="${MAIL_ENV:-$HOME/orrery/mail/.env}"
HTTP_BEARER_MODE="${AGENTSTACK_MAIL_HTTP_BEARER_MODE:-$RESERVATION_INSTALLED_BEARER_MODE}"
HTTP_BEARER_MODE="${HTTP_BEARER_MODE:-auto}"

expand_path() {
    local p="$1"
    if [[ "$p" == "~/"* ]]; then
        printf '%s\n' "$HOME/${p:2}"
    else
        printf '%s\n' "$p"
    fi
}

# Returns "<source>|<name>". Keeping the source distinguishes unresolved from
# an identity conflict, which must remain fail-closed in the reservation guard.
resolve_agent_name() {
    if [[ -f "$HOOKS_DIR/resolve-agent-name.sh" ]]; then
        # shellcheck disable=SC1091
        source "$HOOKS_DIR/resolve-agent-name.sh"
        printf '%s|%s\n' "${RESOLVED_AGENT_SRC:-none}" "${RESOLVED_AGENT:-}"
        return 0
    fi
    if [[ -n "${AGENT_NAME:-}" ]]; then
        printf 'env|%s\n' "$AGENT_NAME"
        return 0
    fi
    printf 'none|\n'
    return 0
}

get_legacy_http_bearer() {
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

legacy_bearer_enabled() {
    case "$HTTP_BEARER_MODE" in
        enabled) return 0 ;;
        disabled) return 1 ;;
        auto)
            case "$MCP_URL" in
                http://127.0.0.1:18765/mcp|http://127.0.0.1:18765/mcp/|http://localhost:18765/mcp|http://localhost:18765/mcp/|http://127.0.0.1:18765/api|http://127.0.0.1:18765/api/|http://localhost:18765/api|http://localhost:18765/api/)
                    return 1
                    ;;
                *) return 0 ;;
            esac
            ;;
        *)
            echo "Invalid AGENTSTACK_MAIL_HTTP_BEARER_MODE: $HTTP_BEARER_MODE" >&2
            return 2
            ;;
    esac
}

reservation_extract_session_id_only() {
    local tool_document="$1"
    SESSION_ID=$(printf '%s' "$tool_document" | python3 -c '
import json, sys
try:
    value = json.loads(sys.stdin.read()).get("session_id", "")
    print(value if isinstance(value, str) else "")
except Exception:
    print("")
' 2>/dev/null || echo "")
    export AGENTSTACK_SESSION_ID="$SESSION_ID"
}

# Resolve the hook invocation's actual workspace and the Mail namespace it may
# act in. The payload cwd is the only workspace evidence: the hook process's
# own directory can belong to another session or repository, so a payload
# without an absolute, existing cwd is unresolved rather than guessed.
#
# A selected key (live AGENTSTACK_PROJECT_KEY/PROJECT_KEY, else the installed
# one) must validate against that workspace; with no selection the key derived
# from the workspace is used. Either way the namespace is the validated
# context's canonical project_key, never the selection's own spelling.
# Protection always starts at the actual worktree root, so configured roots can
# add same-repository worktrees but can never replace the workspace edited.
#
# Sets RESERVATION_PROJECT_KEY, RESERVATION_WORK_DIR and PROTECTED_ROOTS.
# Returns 2 when the workspace is missing or invalid and 3 when the selected
# project does not belong to it. Callers must not classify paths or contact
# Mail after a nonzero return.
reservation_resolve_workspace() {
    local tool_document="$1" cwd_state="" target="" work_dir="" selected=""
    local context="" repository="" workspace_root="" configured="" root=""
    local root_context="" old_ifs=""
    RESERVATION_PROJECT_KEY=""
    RESERVATION_WORK_DIR=""
    PROTECTED_ROOTS=""
    cwd_state=$(printf '%s' "$tool_document" | python3 -c '
import json, os, sys
try:
    document = json.loads(sys.stdin.read())
except Exception:
    document = {}
value = document.get("cwd") if isinstance(document, dict) else None
ok = isinstance(value, str) and os.path.isabs(value) and "\n" not in value
print(("value:" + value) if ok else "invalid:")
' 2>/dev/null || echo "invalid:")
    case "$cwd_state" in
        value:*) target="${cwd_state#value:}" ;;
        *) return 2 ;;
    esac
    work_dir="$(agentstack_physical_dir "$target")" || return 2

    selected="$(agentstack_resolve_project_key "" "" 0)"
    if [ -n "$selected" ]; then
        context="$(agentstack_validate_project_context "$work_dir" "$selected" 2>/dev/null)" || return 3
    else
        context="$(agentstack_resolve_invocation_context "$work_dir" 2>/dev/null)" || return 2
    fi
    RESERVATION_PROJECT_KEY="$(agentstack_context_field "$context" project_key)" || return 2
    repository="$(agentstack_context_field "$context" repository_key)" || return 2
    workspace_root="$(agentstack_context_field "$context" worktree_root)" || return 2
    if [ -z "$workspace_root" ]; then
        # A non-Git project root is the validated selection when it names one;
        # validation has already proved the workspace lies inside it.
        if [ -n "$selected" ] && [ -d "$selected" ]; then
            workspace_root="$(agentstack_physical_dir "$selected")" || return 2
        elif [ -n "$selected" ]; then
            workspace_root="$(agentstack_physical_dir "${AGENTSTACK_PROJECT_WORK_DIR:-}")" || return 2
        else
            workspace_root="$work_dir"
        fi
    fi
    [ -n "$RESERVATION_PROJECT_KEY" ] && [ -n "$workspace_root" ] || return 2
    PROTECTED_ROOTS="$workspace_root"

    configured="$(agentstack_resolve_protected_roots "$RESERVATION_PROJECT_KEY" \
        "${AGENTSTACK_PROJECT_KEY:-${PROJECT_KEY:-}}")"
    if [ -n "$repository" ] && [ -n "$configured" ]; then
        old_ifs="$IFS"
        IFS=":"
        for root in $configured; do
            IFS="$old_ifs"
            root="$(agentstack_physical_dir "$(expand_path "$root")" 2>/dev/null)" || continue
            [ "$root" != "$workspace_root" ] || continue
            root_context="$(agentstack_resolve_invocation_context "$root" 2>/dev/null)" || continue
            # Only another worktree root of this same repository shares the
            # namespace and its relative paths; anything else is not ours.
            [ "$(agentstack_context_field "$root_context" repository_key)" = "$repository" ] || continue
            [ "$(agentstack_context_field "$root_context" worktree_root)" = "$root" ] || continue
            PROTECTED_ROOTS="$PROTECTED_ROOTS:$root"
        done
        IFS="$old_ifs"
    fi
    RESERVATION_WORK_DIR="$work_dir"
    export AGENTSTACK_LOOKUP_PROJECT_KEY="$RESERVATION_PROJECT_KEY"
    return 0
}

# Populate SESSION_ID, FILE_PATH, MATCHED_ROOT, REL_PATH, and
# RESERVATION_PROJECT_KEY from an Edit/Write hook document. Return 1 for the
# intentional no-op cases (no file, or a file whose physical location is
# outside every protected root), and 2/3 as reservation_resolve_workspace does.
reservation_resolve_tool_context() {
    local tool_document="$1" resolved="" status=0
    reservation_extract_session_id_only "$tool_document"
    FILE_PATH=$(printf '%s' "$tool_document" | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
    tool_input = data.get("tool_input", {})
    value = tool_input.get("file_path", tool_input.get("path", ""))
    print(value if isinstance(value, str) else "")
except Exception:
    print("")
' 2>/dev/null || echo "")
    [ -n "$FILE_PATH" ] || return 1

    reservation_resolve_workspace "$tool_document" || return $?

    # Symlinks and ".." are resolved before matching, so a path spelled under a
    # protected root cannot stand for a file somewhere else, and vice versa.
    resolved=$(QUERY_FILE="$FILE_PATH" QUERY_BASE="$RESERVATION_WORK_DIR" \
        QUERY_ROOTS="$PROTECTED_ROOTS" QUERY_HOME="$HOME" python3 -c '
import os
raw = os.environ["QUERY_FILE"]
if raw.startswith("~/"):
    raw = os.path.join(os.environ["QUERY_HOME"], raw[2:])
elif not os.path.isabs(raw):
    raw = os.path.join(os.environ["QUERY_BASE"], raw)
absolute = os.path.realpath(raw)
for root in os.environ["QUERY_ROOTS"].split(":"):
    if not root:
        continue
    try:
        inside = os.path.commonpath([absolute, root]) == root
    except ValueError:
        inside = False
    if inside:
        relative = os.path.relpath(absolute, root)
        if relative == ".":
            relative = os.path.basename(absolute)
        print(root + "\n" + absolute + "\n" + relative)
        break
' 2>/dev/null)
    status=$?
    [ "$status" -eq 0 ] || return 2
    [ -n "$resolved" ] || return 1
    MATCHED_ROOT="$(printf '%s\n' "$resolved" | sed -n '1p')"
    FILE_PATH="$(printf '%s\n' "$resolved" | sed -n '2p')"
    REL_PATH="$(printf '%s\n' "$resolved" | sed -n '3p')"
    [ -n "$MATCHED_ROOT" ] && [ -n "$FILE_PATH" ] && [ -n "$REL_PATH" ] || return 2
    return 0
}

# Session-scoped hooks (SessionEnd, reservation-tool PreToolUse) have no file
# path, but still act only in the validated namespace of their own workspace.
reservation_extract_session_id() {
    local tool_document="$1"
    reservation_extract_session_id_only "$tool_document"
    reservation_resolve_workspace "$tool_document"
}

# Name one debounce slot. The project namespace is part of it, so the same
# agent name editing the same relative path in two projects never cancels the
# other project's pending release.
reservation_debounce_key() {
    QUERY_PROJECT_KEY="$1" QUERY_AGENT="$2" QUERY_REL_PATH="$3" python3 -c '
import hashlib
import os
import unicodedata

parts = (
    os.environ["QUERY_PROJECT_KEY"],
    os.environ["QUERY_AGENT"],
    unicodedata.normalize("NFC", os.environ["QUERY_REL_PATH"]),
)
print(hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest())
'
}

reservation_failure_log() {
    local detail="$1"
    local log_file="$RUNTIME_DIR/release-failures.log"
    mkdir -p "$RUNTIME_DIR" 2>/dev/null || return 0
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$detail" >> "$log_file" 2>/dev/null || true
}

# A sleeping release worker re-sources this file when its grace period ends,
# so its request is judged by the current rules even if an older hook armed it.
# The worker names its debounce slot in QUERY_STATE_FILE and its generation in
# QUERY_STATE_TOKEN. Either marker means worker mode, and then the slot must be
# exactly $RUNTIME_DIR/file_release_debounce/<reservation_debounce_key> for
# this project, agent and first (NFC) relative path, and must still hold the
# worker's token. A worker armed before slots carried the project cannot show
# which project it was cancelled for, so it becomes a no-op instead of
# releasing a reservation that may have been taken again meanwhile. Calls with
# neither marker (immediate release, SessionEnd) are unaffected.
reservation_worker_slot_matches() {
    local agent="$1" project_key="$2" paths_json="$3" relative="" expected=""
    [ -n "${QUERY_STATE_FILE+x}" ] || [ -n "${QUERY_STATE_TOKEN+x}" ] || return 0
    [ -n "${QUERY_STATE_FILE:-}" ] && [ -n "${QUERY_STATE_TOKEN:-}" ] || return 1
    [ -n "$paths_json" ] || return 1
    relative="$(QUERY_PATHS_JSON="$paths_json" python3 -c '
import json, os
paths = json.loads(os.environ["QUERY_PATHS_JSON"])
if not isinstance(paths, list) or not paths or not isinstance(paths[0], str) or not paths[0]:
    raise SystemExit(1)
print(paths[0])
' 2>/dev/null)" || return 1
    expected="$(reservation_debounce_key "$project_key" "$agent" "$relative" 2>/dev/null)" || return 1
    [ -n "$expected" ] || return 1
    [ "$QUERY_STATE_FILE" = "$RUNTIME_DIR/file_release_debounce/$expected" ] || return 1
    # The slot may have been re-armed or invalidated between the worker's own
    # check and this call; only the generation it was started for may release.
    QUERY_STATE_FILE="$QUERY_STATE_FILE" QUERY_STATE_TOKEN="$QUERY_STATE_TOKEN" python3 -c '
import os, stat
path = os.environ["QUERY_STATE_FILE"]
try:
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(1)
    with open(path, encoding="utf-8") as handle:
        current = handle.read().strip()
except (OSError, UnicodeError):
    raise SystemExit(1)
raise SystemExit(0 if current == os.environ["QUERY_STATE_TOKEN"] else 1)
' 2>/dev/null
}

# Send release_file_reservations. The third argument is a JSON list of paths;
# omit it to release every reservation owned by the agent. Errors are durable.
reservation_release_request() {
    local agent="$1"
    local project_key="$2"
    local paths_json="${3:-}"
    local token=""
    if ! reservation_worker_slot_matches "$agent" "$project_key" "$paths_json"; then
        reservation_failure_log "release agent=$agent project=$project_key error=unscoped-release-worker"
        return 1
    fi
    if legacy_bearer_enabled; then
        token="$(get_legacy_http_bearer 2>/dev/null || true)"
    else
        local bearer_status=$?
        if [ "$bearer_status" -eq 2 ]; then
            reservation_failure_log "release agent=$agent project=$project_key error=invalid-bearer-mode"
            return 1
        fi
    fi
    QUERY_PROJECT_KEY="$project_key" QUERY_AGENT="$agent" \
        QUERY_PATHS_JSON="$paths_json" QUERY_TOKEN="$token" QUERY_URL="$MCP_URL" \
        QUERY_FAILURE_LOG="$RUNTIME_DIR/release-failures.log" python3 - <<'PY'
import datetime
import json
import os
import socket
import urllib.error
import urllib.request

agent = os.environ["QUERY_AGENT"]
project_key = os.environ["QUERY_PROJECT_KEY"]
url = os.environ.get("QUERY_URL", "http://127.0.0.1:18765/api/")
token = os.environ.get("QUERY_TOKEN", "")
paths_json = os.environ.get("QUERY_PATHS_JSON", "")
log_path = os.environ["QUERY_FAILURE_LOG"]

def fail(detail):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe = " ".join(str(detail).splitlines())
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} release agent={agent} project={project_key} error={safe}\n")
    except OSError:
        pass
    print(safe)
    raise SystemExit(1)

arguments = {"project_key": project_key, "agent_name": agent}
if paths_json:
    try:
        paths = json.loads(paths_json)
    except Exception as exc:
        fail(f"invalid paths JSON: {exc}")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        fail("paths must be a JSON string list")
    arguments["paths"] = paths

payload = json.dumps({
    "jsonrpc": "2.0",
    "id": "reservation-release",
    "method": "tools/call",
    "params": {"name": "release_file_reservations", "arguments": arguments},
}).encode()
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
if token:
    headers["Authorization"] = "Bearer " + token
request = urllib.request.Request(url, data=payload, headers=headers)
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode()
except urllib.error.HTTPError as exc:
    detail = exc.read().decode(errors="replace").strip()
    fail(f"HTTP {exc.code}: {detail or exc.reason}")
except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout) as exc:
    fail(f"transport: {exc}")
except Exception as exc:
    fail(f"request failure: {exc}")

try:
    document = json.loads(body)
except Exception as exc:
    fail(f"invalid JSON response: {exc}")
if document.get("error") is not None:
    fail("MCP error: " + json.dumps(document["error"], sort_keys=True))
result = document.get("result")
if not isinstance(result, dict):
    fail("MCP result is not an object")
if result.get("isError") is True:
    fail("MCP tool result reports isError=true")
PY
}
