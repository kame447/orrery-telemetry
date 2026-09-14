#!/usr/bin/env bash
# Shared ORRERY Mail registration helpers for agent launchers.
# SOURCE this file; callers are expected to run with set -euo pipefail.

if [ -n "${BASH_SOURCE:-}" ]; then _ags_register_src="${BASH_SOURCE[0]}"; else _ags_register_src="$0"; fi
AGS_REGISTER_LIB_DIR="$(cd "$(dirname "$_ags_register_src")" && pwd)"
# shellcheck source=agentstack-scientists.sh
. "$AGS_REGISTER_LIB_DIR/agentstack-scientists.sh"

AGS_PROJECT_CONTEXT_LIB="${AGENTSTACK_PROJECT_CONTEXT_LIB:-$AGS_REGISTER_LIB_DIR/../../hooks/project-context.sh}"
if [[ -f "$AGS_PROJECT_CONTEXT_LIB" ]] && \
   ! declare -F agentstack_resolve_invocation_context >/dev/null 2>&1; then
  # shellcheck disable=SC1090
  . "$AGS_PROJECT_CONTEXT_LIB"
fi

ags_mail_load_token() {
  local mail_env="${AGENTSTACK_MAIL_ENV:-${MAIL_ENV:-}}"
  if [[ -z "${MCP_AGENT_MAIL_TOKEN:-}" && -n "$mail_env" && -f "$mail_env" ]]; then
    local tok
    tok="$(grep HTTP_BEARER_TOKEN "$mail_env" 2>/dev/null | cut -d= -f2- || true)"
    [[ -n "$tok" ]] && export MCP_AGENT_MAIL_TOKEN="$tok"
  fi
  # The bearer token is optional (tokenless transports set
  # AGENTSTACK_MAIL_HTTP_BEARER_MODE=disabled). Without this the last evaluated
  # command is the `[[ -n "$tok" ]]` test, so "no token" returns 1 and the
  # `set -e` in every caller kills the launcher with no output at all.
  return 0
}

ags_mcp_call() {
  local tool="$1"; shift
  local mcp_url="${AGENTSTACK_MCP_URL:-${MCP_URL:-http://127.0.0.1:18765/mcp}}"
  local args_json payload
  args_json="$(printf '%s\0' "$@" | python3 -c '
import json
import sys

args = {}
for raw in sys.stdin.buffer.read().split(b"\0"):
    if not raw:
        continue
    key, value = raw.decode("utf-8").split("=", 1)
    args[key] = value
print(json.dumps(args, separators=(",", ":")))
')"
  payload="$(printf '%s' "$args_json" | python3 -c '
import json
import sys

print(json.dumps({
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {"name": sys.argv[1], "arguments": json.load(sys.stdin)},
}, separators=(",", ":")))
' "$tool")"
  local auth=()
  [[ -n "${MCP_AGENT_MAIL_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer $MCP_AGENT_MAIL_TOKEN")
  # `${auth[@]+"${auth[@]}"}` — macOS bash 3.2 treats a plain `"${auth[@]}"` on
  # an empty array as an unbound variable under `set -u`, so a tokenless call
  # would abort here instead of sending no Authorization header.
  printf '%s' "$payload" | curl -sf --max-time 30 -X POST "$mcp_url" \
    -H "Content-Type: application/json" -H "Accept: application/json" -H "Connection: close" \
    ${auth[@]+"${auth[@]}"} \
    --data-binary @- 2>/dev/null
}

ags_mcp_has_error() {
  # Exit 0 (== "has error") when the MCP response signals failure at EITHER the
  # JSON-RPC layer (top-level "error") OR the tool layer. A tool failure comes
  # back as a normal JSON-RPC result with result.isError == true and a
  # content[].text like "Error calling tool '...': ...", which a top-level
  # "error" check alone silently passes through.
  python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if data.get("error"):
    sys.exit(0)
result = data.get("result") if isinstance(data, dict) else None
if isinstance(result, dict):
    if result.get("isError") is True:
        sys.exit(0)
    content = result.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.startswith("Error calling tool"):
                    sys.exit(0)
sys.exit(1)
'
}

# The ORRERY Mail row id from a register_agent reply (plain, structuredContent,
# or a JSON text block). Empty when the reply carries none. The id is the key
# of the session index, which is why a shell-side registration needs it.
ags_extract_agent_id() {
  python3 -c '
import json, sys

def candidate_id(obj):
    if isinstance(obj, dict) and isinstance(obj.get("id"), int):
        return obj["id"]
    return None

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)

found = candidate_id(data)
result = data.get("result") if isinstance(data, dict) else None
if found is None:
    found = candidate_id(result)
if found is None and isinstance(result, dict):
    found = candidate_id(result.get("structuredContent"))
    if found is None and isinstance(result.get("content"), list):
        for part in result["content"]:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                try:
                    found = candidate_id(json.loads(part["text"]))
                except Exception:
                    found = None
                if found is not None:
                    break
print("" if found is None else found)
'
}

ags_extract_agent_name() {
  python3 -c '
import json, sys

def candidate_names(obj):
    if isinstance(obj, dict):
        for key in ("name", "agent_name"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                yield value

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)

for name in candidate_names(data):
    print(name)
    sys.exit(0)

result = data.get("result") if isinstance(data, dict) else None
for name in candidate_names(result):
    print(name)
    sys.exit(0)
if isinstance(result, dict):
    structured = result.get("structuredContent")
    for name in candidate_names(structured):
        print(name)
        sys.exit(0)
    content = result.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str):
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            for name in candidate_names(obj):
                print(name)
                sys.exit(0)
print("")
'
}

ags_extract_registration_token() {
  python3 -c '
import json, sys

def candidate_tokens(obj):
    if isinstance(obj, dict):
        value = obj.get("registration_token")
        if isinstance(value, str) and value:
            yield value

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)

for token in candidate_tokens(data):
    print(token)
    sys.exit(0)

result = data.get("result") if isinstance(data, dict) else None
for token in candidate_tokens(result):
    print(token)
    sys.exit(0)
if isinstance(result, dict):
    structured = result.get("structuredContent")
    for token in candidate_tokens(structured):
        print(token)
        sys.exit(0)
    content = result.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str):
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            for token in candidate_tokens(obj):
                print(token)
                sys.exit(0)
print("")
'
}

ags_generate_registration_token() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
}

ags_registration_runtime_dir() {
  printf '%s\n' "${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
}

ags_agent_token_key() {
  printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9_.-' '_'
}

ags_registration_token_file() {
  local agent_name="$1" runtime_dir key
  [[ -n "$agent_name" ]] || return 1
  runtime_dir="$(ags_registration_runtime_dir)"
  key="$(ags_agent_token_key "$agent_name")"
  [[ -n "$key" ]] || return 1
  printf '%s/agent_token_%s\n' "$runtime_dir" "$key"
}

ags_registration_owner_path() {
  local agent_name="$1" runtime_dir key
  [[ -n "$agent_name" ]] || return 1
  runtime_dir="$(ags_registration_runtime_dir)"
  key="$(ags_agent_token_key "$agent_name")"
  [[ -n "$key" && "$key" != "." && "$key" != ".." ]] || return 1
  printf '%s/agent_owner_%s.json\n' "$runtime_dir" "$key"
}

# All case/hyphen spellings that share a host-global name_key must contend on
# one filesystem claim. Public owner/token filenames stay exact for backward
# compatibility; only the internal pending lock is folded.
ags_registration_claim_path() {
  local agent_name="$1" runtime_dir key folded
  [[ -n "$agent_name" ]] || return 1
  runtime_dir="$(ags_registration_runtime_dir)"
  key="$(ags_agent_token_key "$agent_name")"
  folded="$(printf '%s' "$key" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')"
  [[ -n "$folded" && "$folded" != "." && "$folded" != ".." ]] || return 1
  printf '%s/agent_owner_claim_%s.pending\n' "$runtime_dir" "$folded"
}

ags_legacy_registration_owner_file() {
  local agent_name="$1" runtime_dir key
  [[ -n "$agent_name" ]] || return 1
  runtime_dir="$(ags_registration_runtime_dir)"
  key="$(ags_agent_token_key "$agent_name")"
  [[ -n "$key" && "$key" != "." && "$key" != ".." ]] || return 1
  printf '%s/child-agents/%s.json\n' "$runtime_dir" "$key"
}

ags_normalize_project_key() {
  local project_key="${1:-}"
  if declare -F agentstack_normalize_project_key >/dev/null 2>&1; then
    agentstack_normalize_project_key "$project_key"
    return
  fi
  "${AGENTSTACK_PYTHON:-python3}" - "$project_key" <<'PY'
import pathlib
import sys

value = sys.argv[1]
path = pathlib.Path(value)
print(path.resolve() if value and (path.is_absolute() or path.is_dir()) else value)
PY
}

ags_project_keys_equal() {
  local left="" right=""
  left="$(ags_normalize_project_key "${1:-}")" || return 1
  right="$(ags_normalize_project_key "${2:-}")" || return 1
  [[ "$left" == "$right" ]]
}

ags_registration_context_project_key() {
  local context_json="${1:-}"
  "${AGENTSTACK_PYTHON:-python3}" - "$context_json" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
    value = data["project_key"]
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
if not isinstance(value, str) or not value or any(
    ord(char) < 32 or ord(char) == 127 for char in value
):
    raise SystemExit(1)
print(value)
PY
}

# Return a fresh context for TARGET only when the durable owner record proves
# the agent/token/project association. Git repository identity is shared by
# linked worktrees, so a strong owner may resume in any worktree of that same
# repository. Non-Git ownership is bound to an explicit physical root.
ags_registration_owner_context() {
  local agent_name="$1" registration_token="$2" target="$3"
  local owner_file="" legacy_file="" project_key="" actual_context=""
  owner_file="$(ags_registration_owner_path "$agent_name")" || return 1
  [[ -f "$owner_file" && ! -L "$owner_file" ]] || return 1
  project_key="$("${AGENTSTACK_PYTHON:-python3}" - "$owner_file" \
    "$agent_name" 3<<<"$registration_token" <<'PY'
import hashlib
import json
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
agent_name = sys.argv[2]
token = os.read(3, 4097).decode("utf-8").rstrip("\n")
try:
    mode = stat.S_IMODE(path.stat().st_mode)
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if mode & 0o077 or not isinstance(data, dict):
    raise SystemExit(1)
folded = agent_name.replace("-", "").casefold()
if data.get("schema") != 1 or data.get("agent_name") != agent_name:
    raise SystemExit(1)
if data.get("name_key", folded) != folded:
    raise SystemExit(1)
if data.get("token_sha256") != hashlib.sha256(token.encode("utf-8")).hexdigest():
    raise SystemExit(1)
project = data.get("project_key")
if not isinstance(project, str) or not project:
    raise SystemExit(1)
print(project)
PY
)" || return 1
  actual_context="$(agentstack_resolve_invocation_context "$target" "$project_key")" \
    || return 1
  legacy_file="$(ags_legacy_registration_owner_file "$agent_name")" || return 1
  "${AGENTSTACK_PYTHON:-python3}" - "$owner_file" "$legacy_file" \
    "$actual_context" 3<<<"$registration_token" <<'PY'
import json
import os
import pathlib
import sys

owner = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
legacy_path = pathlib.Path(sys.argv[2])
actual = json.loads(sys.argv[3])
token = os.read(3, 4097).decode("utf-8").rstrip("\n")
def normalize_key(value):
    if not isinstance(value, str) or not value:
        return ""
    path = pathlib.Path(value)
    return str(path.resolve()) if path.is_absolute() or path.is_dir() else value
if owner.get("project_key") != actual.get("project_key"):
    raise SystemExit(1)
repository = owner.get("repository_key")
non_git_root = owner.get("non_git_root")
if repository is not None:
    if not isinstance(repository, str) or not repository:
        raise SystemExit(1)
    if actual.get("repository_key") != repository or non_git_root is not None:
        raise SystemExit(1)
else:
    if actual.get("repository_key") is not None:
        raise SystemExit(1)
    if not isinstance(non_git_root, str) or not non_git_root:
        raise SystemExit(1)
    try:
        pathlib.Path(actual["work_dir"]).relative_to(pathlib.Path(non_git_root))
    except (KeyError, TypeError, ValueError):
        raise SystemExit(1)
if actual.get("protected_roots") != [actual.get("worktree_root") or actual.get("work_dir")]:
    raise SystemExit(1)

# Weak state never outranks a strong record. If both remain during migration,
# any disagreement is corruption rather than a reason to fall back.
if legacy_path.exists() or legacy_path.is_symlink():
    if legacy_path.is_symlink():
        raise SystemExit(1)
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception:
        raise SystemExit(1)
    if not isinstance(legacy, dict):
        raise SystemExit(1)
    if legacy.get("agent_name") not in (None, owner.get("agent_name")):
        raise SystemExit(1)
    if normalize_key(legacy.get("project_key")) != normalize_key(owner.get("project_key")):
        raise SystemExit(1)
    if legacy.get("registration_token") != token:
        raise SystemExit(1)
print(json.dumps(actual, separators=(",", ":")))
PY
}

# Compatibility proof for children created before registration-owner records.
# The child state contains the name, project and the same owner token.  A path
# project must still describe TARGET's repository/workspace; a logical key has
# no repository proof and is intentionally not upgraded here.
ags_legacy_registration_owner_context() {
  local agent_name="$1" registration_token="$2" target="$3"
  local runtime_dir="" state_file="" project_key="" actual_context=""
  local project_repository="" target_repository=""
  runtime_dir="$(ags_registration_runtime_dir)"
  state_file="$(ags_legacy_registration_owner_file "$agent_name")" || return 1
  [[ -f "$state_file" && ! -L "$state_file" ]] || return 1
  project_key="$("${AGENTSTACK_PYTHON:-python3}" - "$state_file" \
    "$agent_name" 3<<<"$registration_token" <<'PY'
import json
import os
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
try:
    mode = stat.S_IMODE(path.stat().st_mode)
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if mode & 0o077 or not isinstance(data, dict):
    raise SystemExit(1)
if data.get("agent_name") not in (None, sys.argv[2]):
    raise SystemExit(1)
token = os.read(3, 4097).decode("utf-8").rstrip("\n")
if data.get("registration_token") != token:
    raise SystemExit(1)
project = data.get("project_key")
if not isinstance(project, str) or not project:
    raise SystemExit(1)
print(project)
PY
)" || return 1
  [[ -d "$target" ]] || return 1
  if [[ -d "$project_key" ]]; then
    project_repository="$(agentstack_repository_key "$project_key" 2>/dev/null || true)"
    target_repository="$(agentstack_repository_key "$target" 2>/dev/null || true)"
    [[ -n "$project_repository" && "$project_repository" == "$target_repository" ]] \
      || return 1
  else
    return 1
  fi
  actual_context="$(agentstack_resolve_invocation_context "$target" "$project_key")" \
    || return 1
  printf '%s\n' "$actual_context"
}

ags_registration_owner_exists_for_name() {
  local agent_name="$1" owner_file=""
  owner_file="$(ags_registration_owner_path "$agent_name" 2>/dev/null || true)"
  [[ -n "$owner_file" && ( -e "$owner_file" || -L "$owner_file" ) ]]
}

ags_legacy_registration_owner_exists_for_name() {
  local agent_name="$1" state_file=""
  state_file="$(ags_legacy_registration_owner_file "$agent_name" 2>/dev/null || true)"
  [[ -n "$state_file" && ( -e "$state_file" || -L "$state_file" ) ]]
}

ags_verify_registration_owner_with_mail() {
  local project_key="$1" agent_name="$2" registration_token="$3"
  local response="" returned=""
  [[ -n "$project_key" && -n "$agent_name" && -n "$registration_token" ]] || return 1
  response="$(ags_mcp_call "whois" \
    "project_key=$project_key" \
    "agent_name=$agent_name" \
    "registration_token=$registration_token" 2>/dev/null || true)"
  [[ -n "$response" ]] || return 1
  ! printf '%s' "$response" | ags_mcp_has_error || return 1
  returned="$(printf '%s' "$response" | ags_extract_agent_name)"
  [[ "$returned" == "$agent_name" ]]
}

# Resolve the context used by ensure_project/register_agent.  The only custom
# namespace authorities are an explicit launcher transport or a persisted
# owner proof.  Otherwise the actual target is resolved with no ambient or
# installed fallback, and a caller-supplied different namespace is refused.
ags_resolve_registration_context() {
  local supplied_project="$1" work_dir="$2" agent_name="${3:-}"
  local requested_mode="${4:-candidate}" registration_token="${5:-}"
  local invocation_transport="${6:-}" context_json="" context_project="" transport_context=""
  local owner_exists=0
  declare -F agentstack_resolve_invocation_context >/dev/null 2>&1 || return 1

  if [[ "$requested_mode" == "reserved" && -n "$agent_name" && -n "$registration_token" ]]; then
    if ags_registration_owner_exists_for_name "$agent_name"; then
      owner_exists=1
    fi
    context_json="$(ags_registration_owner_context \
      "$agent_name" "$registration_token" "$work_dir" 2>/dev/null || true)"
    if [[ -z "$context_json" && "$owner_exists" == "1" ]]; then
      echo "agentstack: persisted ownership for '$agent_name' does not match its token or target; refusing registration." >&2
      return 1
    fi
    if [[ -z "$context_json" ]]; then
      context_json="$(ags_legacy_registration_owner_context \
        "$agent_name" "$registration_token" "$work_dir" 2>/dev/null || true)"
      if [[ -z "$context_json" ]] && \
         ags_legacy_registration_owner_exists_for_name "$agent_name"; then
        echo "agentstack: legacy ownership for '$agent_name' is cross-repository, ambiguous, or corrupt; relaunch it with an explicit project namespace." >&2
        return 1
      fi
    fi
    if [[ -z "$context_json" ]]; then
      echo "agentstack: reserved identity '$agent_name' has no persisted workspace ownership; refusing registration." >&2
      return 1
    fi
    # A top-level invocation envelope can corroborate a reserved owner, never
    # replace it. Resolve the owner/default recovery first, then require exact
    # tuple equality so an explicit key cannot escape a durable non-Git root.
    if [[ -n "$invocation_transport" ]]; then
      transport_context="$(agentstack_validate_invocation_transport \
        "$invocation_transport" "$work_dir")" || return 1
      agentstack_contexts_equal "$context_json" "$transport_context" || {
        echo "agentstack: invocation transport contradicts reserved ownership for '$agent_name'." >&2
        return 1
      }
    fi
  elif [[ -n "$invocation_transport" ]]; then
    context_json="$(agentstack_validate_invocation_transport \
      "$invocation_transport" "$work_dir")" || return 1
  fi
  if [[ -z "$context_json" ]]; then
    context_json="$(agentstack_resolve_invocation_context "$work_dir")" || return 1
  fi
  context_project="$(ags_registration_context_project_key "$context_json")" \
    || return 1
  if [[ -n "$supplied_project" ]] && \
     ! ags_project_keys_equal "$supplied_project" "$context_project"; then
    echo "agentstack: project '$supplied_project' is not authorized for work directory '$work_dir'; resolved ownership is '$context_project'." >&2
    return 1
  fi
  AGS_REGISTRATION_CONTEXT_JSON="$context_json"
  AGS_REGISTRATION_PROJECT_KEY="$context_project"
  export AGS_REGISTRATION_CONTEXT_JSON AGS_REGISTRATION_PROJECT_KEY
  printf '%s\n' "$context_json"
}

# Atomically claim the host-global local name before Mail registration. The
# pending claim is separate from the authoritative owner record: a strong
# record is published only after Mail confirms the accepted canonical identity.
ags_begin_registration_ownership() {
  local context_json="$1" agent_name="$2" registration_token="$3"
  local ownership_kind="${4:-top-level}" mode="${5:-candidate}"
  local owner_file="" pending_file="" owner_dir="" project_key="" result=""
  owner_file="$(ags_registration_owner_path "$agent_name")" || return 1
  pending_file="$(ags_registration_claim_path "$agent_name")" || return 1
  owner_dir="$(dirname "$owner_file")"
  mkdir -p "$owner_dir" || return 1
  chmod 700 "$owner_dir" 2>/dev/null || true
  result="$("${AGENTSTACK_PYTHON:-python3}" - "$owner_file" "$pending_file" "$context_json" \
    "$agent_name" "$ownership_kind" "$mode" 3<<<"$registration_token" <<'PY'
import hashlib
import json
import os
import pathlib
import sys
import time

path = pathlib.Path(sys.argv[1])
pending_path = pathlib.Path(sys.argv[2])
context = json.loads(sys.argv[3])
agent_name, kind, mode = sys.argv[4:7]
token = os.read(3, 4097).decode("utf-8").rstrip("\n")
name_key = agent_name.replace("-", "").casefold()
if not name_key or not token:
    raise SystemExit(1)
payload = {
    "schema": 1,
    "agent_name": agent_name,
    "name_key": name_key,
    "project_key": context.get("project_key"),
    "repository_key": context.get("repository_key"),
    "non_git_root": context.get("work_dir") if context.get("repository_key") is None else None,
    "created_by": kind,
    "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
if not isinstance(payload["project_key"], str) or not payload["project_key"]:
    raise SystemExit(1)
if payload["repository_key"] is None and not payload["non_git_root"]:
    raise SystemExit(1)
if path.exists() or path.is_symlink():
    if path.is_symlink():
        raise SystemExit(1)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise SystemExit(1)
    if mode != "reserved" or existing.get("schema") != 1:
        raise SystemExit(1)
    if existing.get("agent_name") != agent_name:
        raise SystemExit(1)
    if existing.get("project_key") != payload["project_key"]:
        raise SystemExit(1)
    if existing.get("repository_key") != payload["repository_key"]:
        raise SystemExit(1)
    if existing.get("repository_key") is None:
        root = existing.get("non_git_root")
        if not isinstance(root, str) or not root:
            raise SystemExit(1)
        try:
            pathlib.Path(context["work_dir"]).relative_to(pathlib.Path(root))
        except (KeyError, TypeError, ValueError):
            raise SystemExit(1)
    elif existing.get("non_git_root") is not None:
        raise SystemExit(1)
    if existing.get("token_sha256") != payload["token_sha256"]:
        raise SystemExit(1)
    print("existing")
    raise SystemExit(0)
try:
    fd = os.open(pending_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit(1)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(pending_path, 0o600)
print("created")
PY
)" || return 1
  [[ "$result" == "created" || "$result" == "existing" ]] || return 1
  AGS_REGISTRATION_OWNER_FILE="$owner_file"
  AGS_REGISTRATION_PENDING_FILE="$pending_file"
  AGS_REGISTRATION_OWNER_CREATED=0
  [[ "$result" == "created" ]] && AGS_REGISTRATION_OWNER_CREATED=1
  if [[ "$result" == "created" ]]; then
    # Preflight and O_EXCL are not one transaction: another spelling can
    # publish its durable owner after our preflight, then drop this shared
    # folded claim before we acquire it. Recheck while holding the claim.
    project_key="$(ags_registration_context_project_key "$context_json")" || {
      ags_release_registration_ownership
      return 1
    }
    local held_mode="claim-holder"
    [[ "$mode" == "reserved" ]] && held_mode="claim-holder-reserved"
    if ags_local_agent_name_conflicts "$project_key" "$agent_name" "$held_mode"; then
      ags_release_registration_ownership
      return 1
    fi
  fi
  return 0
}

ags_release_registration_ownership() {
  local owner_file="${AGS_REGISTRATION_OWNER_FILE:-}"
  local pending_file="${AGS_REGISTRATION_PENDING_FILE:-}"
  if [[ "${AGS_REGISTRATION_OWNER_CREATED:-0}" == "1" && -n "$pending_file" ]]; then
    "${AGENTSTACK_PYTHON:-python3}" - "$pending_file" <<'PY' 2>/dev/null || true
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    if path.is_symlink():
        raise SystemExit(1)
    path.unlink()
except FileNotFoundError:
    raise SystemExit(0)
PY
  fi
  AGS_REGISTRATION_OWNER_FILE=""
  AGS_REGISTRATION_PENDING_FILE=""
  AGS_REGISTRATION_OWNER_CREATED=0
}

ags_commit_registration_ownership() {
  local context_json="$1" requested_name="$2" registered_name="$3"
  local registration_token="$4" ownership_kind="${5:-top-level}"
  local old_owner="${AGS_REGISTRATION_OWNER_FILE:-}" pending_file="${AGS_REGISTRATION_PENDING_FILE:-}"
  local new_owner="" token_file="" new_claim=""
  new_owner="$(ags_registration_owner_path "$registered_name")" || return 1
  token_file="$(ags_registration_token_file "$registered_name")" || return 1
  new_claim="$(ags_registration_claim_path "$registered_name")" || return 1
  if ! "${AGENTSTACK_PYTHON:-python3}" - "$old_owner" "$pending_file" "$new_owner" "$token_file" "$new_claim" \
    "$context_json" "$requested_name" "$registered_name" "$ownership_kind" \
    3<<<"$registration_token" <<'PY'
import atexit
import hashlib
import json
import os
import pathlib
import sys
import time

old_path = pathlib.Path(sys.argv[1])
pending_path = pathlib.Path(sys.argv[2]) if sys.argv[2] else None
new_path = pathlib.Path(sys.argv[3])
token_path = pathlib.Path(sys.argv[4])
new_claim_path = pathlib.Path(sys.argv[5])
context = json.loads(sys.argv[6])
requested, registered, kind = sys.argv[7:10]
token = os.read(3, 4097).decode("utf-8").rstrip("\n")
substitution_claim = None

def cleanup_substitution_claim():
    if substitution_claim is not None:
        try:
            substitution_claim.unlink()
        except OSError:
            pass

def folded_name(value):
    return value.replace("-", "").casefold() if isinstance(value, str) else ""

def durable_alias_exists(runtime_dir, target_key):
    # Filename provenance is enough to conflict even when an artifact is
    # corrupt; a valid payload may additionally expose a canonical name.
    for candidate in runtime_dir.glob("agent_owner_*.json"):
        filename_name = candidate.name[len("agent_owner_") : -len(".json")]
        names = [filename_name]
        if candidate.is_file() and not candidate.is_symlink():
            try:
                stored_name = json.loads(candidate.read_text(encoding="utf-8")).get(
                    "agent_name"
                )
            except Exception:
                stored_name = None
            names.append(stored_name)
        if any(folded_name(value) == target_key for value in names):
            return True
    for candidate in runtime_dir.glob("agent_token_*"):
        value = candidate.name[len("agent_token_") :]
        if folded_name(value) == target_key:
            return True
    child_dir = runtime_dir / "child-agents"
    if child_dir.is_dir():
        for candidate in child_dir.glob("*.json"):
            names = [candidate.stem]
            if candidate.is_file() and not candidate.is_symlink():
                try:
                    stored_name = json.loads(
                        candidate.read_text(encoding="utf-8")
                    ).get("agent_name")
                except Exception:
                    stored_name = None
                names.append(stored_name)
            if any(folded_name(value) == target_key for value in names):
                return True
    return False

try:
    prior_raw = old_path.read_bytes()
    prior = json.loads(prior_raw.decode("utf-8"))
except Exception:
    prior_raw = None
    prior = {}
if isinstance(prior, dict) and prior.get("schema") == 1:
    prior_kind = prior.get("created_by")
    if isinstance(prior_kind, str) and prior_kind:
        kind = prior_kind
elif kind == "preserve":
    prior_kind = None
    kind = prior_kind if isinstance(prior_kind, str) and prior_kind else "legacy-resume"
name_key = registered.replace("-", "").casefold()
non_git_root = context.get("work_dir") if context.get("repository_key") is None else None
if (
    isinstance(prior, dict)
    and prior.get("repository_key") is None
    and isinstance(prior.get("non_git_root"), str)
    and prior.get("non_git_root")
):
    non_git_root = prior["non_git_root"]
payload = {
    "schema": 1,
    "agent_name": registered,
    "name_key": name_key,
    "project_key": context.get("project_key"),
    "repository_key": context.get("repository_key"),
    "non_git_root": non_git_root,
    "created_by": kind,
    "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
if not token or (payload["repository_key"] is None and not payload["non_git_root"]):
    raise SystemExit(1)
new_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(new_path.parent, 0o700)

if new_path != old_path:
    if pending_path is None or new_claim_path != pending_path:
        substitution_claim = new_claim_path
        try:
            claim_fd = os.open(
                substitution_claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            raise SystemExit(1)
        with os.fdopen(claim_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(substitution_claim, 0o600)
        atexit.register(cleanup_substitution_claim)
    # The returned-name preflight happened before this claim was acquired.
    # Recheck every durable host-global alias while holding the returned
    # folded claim, including the same-folded substitution case where the
    # original request's claim is reused.
    if durable_alias_exists(new_path.parent, name_key):
        raise SystemExit(1)
    if (
        new_path.exists()
        or new_path.is_symlink()
        or token_path.exists()
        or token_path.is_symlink()
    ):
        raise SystemExit(1)
owner_tmp = new_path.with_name(new_path.name + f".tmp.{os.getpid()}")
token_tmp = token_path.with_name(token_path.name + f".tmp.{os.getpid()}")
with open(owner_tmp, "x", encoding="utf-8") as handle:
    json.dump(payload, handle, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(owner_tmp, 0o600)
with open(token_tmp, "x", encoding="utf-8") as handle:
    handle.write(token)
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(token_tmp, 0o600)
owner_replaced = False
try:
    os.replace(owner_tmp, new_path)
    owner_replaced = True
    os.replace(token_tmp, token_path)
except Exception:
    # The owner record and token are one logical publication. If installing the
    # token fails after the owner rename, restore the previous strong record or
    # remove the new one so no digest points at missing state.
    if owner_replaced:
        try:
            if new_path == old_path and prior_raw is not None:
                rollback = new_path.with_name(new_path.name + f".rollback.{os.getpid()}")
                with open(rollback, "xb") as handle:
                    handle.write(prior_raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(rollback, 0o600)
                os.replace(rollback, new_path)
            else:
                new_path.unlink()
        except OSError:
            pass
    for temporary in (owner_tmp, token_tmp):
        try:
            temporary.unlink()
        except OSError:
            pass
    raise
if pending_path is not None:
    try:
        pending_path.unlink()
    except OSError:
        pass
PY
  then
    return 1
  fi
  AGS_REGISTRATION_OWNER_FILE=""
  AGS_REGISTRATION_PENDING_FILE=""
  AGS_REGISTRATION_OWNER_CREATED=0
}

# True means the local host-global name cannot be claimed for PROJECT_KEY.
# Mail identities are project-local, while tmux sessions and token paths are
# not; candidate names therefore fail closed on any legacy/corrupt artifact.
ags_local_agent_name_conflicts() {
  local project_key="$1" agent_name="$2" mode="${3:-candidate}"
  local runtime_dir="" requested_key="" owner_file="" path="" local_name="" artifact_name=""
  local recorded_project="" current_tmux=""
  [[ -n "$project_key" && -n "$agent_name" ]] || return 0
  runtime_dir="$(ags_registration_runtime_dir)"
  requested_key="$(printf '%s' "$agent_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')"
  owner_file="$(ags_registration_owner_path "$agent_name" 2>/dev/null || true)"
  if [[ -n "$owner_file" && ( -e "$owner_file" || -L "$owner_file" ) ]]; then
    [[ "$mode" == "reserved" && -f "$owner_file" && ! -L "$owner_file" ]] \
      || return 0
  fi
  for path in "$runtime_dir"/agent_owner_*.json; do
    [[ -e "$path" || -L "$path" ]] || continue
    [[ "$path" == "$owner_file" ]] && continue
    artifact_name="${path##*/agent_owner_}"
    artifact_name="${artifact_name%.json}"
    [[ "$(printf '%s' "$artifact_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
      && return 0
    if [[ -L "$path" || ! -f "$path" ]]; then
      local_name="$artifact_name"
    else
      local_name="$("${AGENTSTACK_PYTHON:-python3}" - "$path" <<'PY' 2>/dev/null || true
import json
import sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get("agent_name", "")
except Exception:
    value = ""
print(value if isinstance(value, str) else "")
PY
)"
    fi
    [[ -n "$local_name" ]] || continue
    [[ "$(printf '%s' "$local_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
      && return 0
  done
  for path in "$runtime_dir"/agent_owner_claim_*.pending "$runtime_dir"/agent_owner_*.json.pending; do
    [[ -e "$path" || -L "$path" ]] || continue
    [[ ( "$mode" == "substitution" || "$mode" == claim-holder* ) && \
       "$path" == "${AGS_REGISTRATION_PENDING_FILE:-}" ]] && continue
    artifact_name="${path##*/agent_owner_claim_}"
    artifact_name="${artifact_name##*/agent_owner_}"
    artifact_name="${artifact_name%.json.pending}"
    artifact_name="${artifact_name%.pending}"
    [[ "$(printf '%s' "$artifact_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
      && return 0
    if [[ -L "$path" || ! -f "$path" ]]; then
      local_name="$artifact_name"
    else
      local_name="$("${AGENTSTACK_PYTHON:-python3}" - "$path" <<'PY' 2>/dev/null || true
import json
import sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get("agent_name", "")
except Exception:
    value = ""
print(value if isinstance(value, str) else "")
PY
)"
    fi
    [[ -n "$local_name" ]] || continue
    [[ "$(printf '%s' "$local_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
      && return 0
  done
  for path in "$runtime_dir"/agent_token_*; do
    [[ -e "$path" || -L "$path" ]] || continue
    local_name="$(basename "$path")"
    local_name="${local_name#agent_token_}"
    [[ "$(printf '%s' "$local_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
      || continue
    [[ ( "$mode" == "reserved" || "$mode" == "claim-holder-reserved" ) && \
       "$local_name" == "$agent_name" ]] || return 0
  done
  for path in "$runtime_dir"/child-agents/*.json; do
    [[ -e "$path" || -L "$path" ]] || continue
    local_name="$(basename "$path" .json)"
    [[ "$(printf '%s' "$local_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
      || continue
    [[ ( "$mode" == "reserved" || "$mode" == "claim-holder-reserved" ) && \
       "$local_name" == "$agent_name" ]] || return 0
    recorded_project="$("${AGENTSTACK_PYTHON:-python3}" - "$path" <<'PY' 2>/dev/null || true
import json
import sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get("project_key", "")
except Exception:
    value = ""
print(value if isinstance(value, str) else "")
PY
)"
    [[ -n "$recorded_project" ]] || return 0
    ags_project_keys_equal "$recorded_project" "$project_key" || return 0
  done
  if command -v tmux >/dev/null 2>&1; then
    current_tmux="$(tmux display-message -p '#S' 2>/dev/null || true)"
    while IFS= read -r local_name; do
      [[ -n "$local_name" ]] || continue
      [[ "$(printf '%s' "$local_name" | LC_ALL=C tr -d '-' | LC_ALL=C tr '[:upper:]' '[:lower:]')" == "$requested_key" ]] \
        || continue
      [[ ( "$mode" == "reserved" || "$mode" == "claim-holder-reserved" ) && \
         "$local_name" == "$agent_name" && "$local_name" == "$current_tmux" ]] \
        || return 0
    done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null || true)
  fi
  return 1
}

ags_load_registration_token() {
  local agent_name="$1" token_file token
  token_file="$(ags_registration_token_file "$agent_name")" || return 1
  [[ -f "$token_file" && ! -L "$token_file" ]] || return 1
  "${AGENTSTACK_PYTHON:-python3}" - "$token_file" <<'PY' >/dev/null 2>&1 || return 1
import pathlib
import stat
import sys

mode = stat.S_IMODE(pathlib.Path(sys.argv[1]).stat().st_mode)
raise SystemExit(0 if not mode & 0o077 else 1)
PY
  IFS= read -r token < "$token_file" || true
  [[ -n "$token" ]] || return 1
  printf '%s\n' "$token"
}

ags_store_registration_token() {
  local agent_name="$1" registration_token="$2" context_json="${3:-}"
  local ownership_kind="${4:-top-level}" runtime_dir token_file
  [[ -n "$agent_name" && -n "$registration_token" ]] || return 0
  if [[ -n "$context_json" ]]; then
    if [[ -z "${AGS_REGISTRATION_OWNER_FILE:-}" ]]; then
      ags_begin_registration_ownership "$context_json" "$agent_name" \
        "$registration_token" "$ownership_kind" reserved || return 1
    fi
    ags_commit_registration_ownership "$context_json" "$agent_name" \
      "$agent_name" "$registration_token" "$ownership_kind"
    return $?
  fi
  runtime_dir="$(ags_registration_runtime_dir)"
  token_file="$(ags_registration_token_file "$agent_name")" || return 1
  mkdir -p "$runtime_dir" || return 1
  ( umask 077 && printf '%s' "$registration_token" > "$token_file" ) || return 1
  chmod 600 "$token_file" 2>/dev/null || true
}

# Record that ORRERY Mail granted a different identity than the one requested.
# The dashboard reads this file and says so on the agent, because the only
# other trace is a missing portrait — which reads as a style, not a fault.
# Best effort: a spawn that otherwise worked must not fail over bookkeeping.
ags_record_name_substitution() {
  local registered="$1" requested="$2" runtime_dir store
  [[ -n "$registered" && -n "$requested" && "$registered" != "$requested" ]] || return 0
  runtime_dir="$(ags_registration_runtime_dir)"
  store="$runtime_dir/name-substitutions.json"
  mkdir -p "$runtime_dir" || return 1
  "${AGENTSTACK_PYTHON:-python3}" - "$store" "$registered" "$requested" <<'PY' || return 1
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

store, registered, requested = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
try:
    data = json.loads(store.read_text(encoding="utf-8"))
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
data[registered] = {
    "requested": requested,
    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
}
tmp = store.with_name(store.name + f".{os.getpid()}.tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(store)
PY
}

ags_apply_contact_policy() {
  local project_key="$1" agent_name="$2" registration_token="${3:-}" policy
  [[ -n "$project_key" && -n "$agent_name" && -n "$registration_token" ]] || return 0
  if [[ ${AGENTSTACK_CONTACT_POLICY+x} ]]; then
    policy="$AGENTSTACK_CONTACT_POLICY"
  else
    policy="open"
  fi
  [[ -n "$policy" ]] || return 0
  policy="$(printf '%s' "$policy" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
  case "$policy" in
    closed)
      policy="contacts_only"
      ;;
    skip|none|off|disabled)
      return 0
      ;;
  esac
  # Try WITH the owner token first: legacy deployments gate set_contact_policy
  # on the agent's registration_token, so omitting it makes the call fail and
  # (previously with a bare || true) silently leave the policy at the server
  # default instead of 'open'. But older/lenient servers whose set_contact_policy
  # signature has no registration_token param reject the extra kwarg — so if the
  # token-bearing call errors, retry without it. Both paths are best-effort.
  local resp
  resp="$(ags_mcp_call "set_contact_policy" \
    "project_key=$project_key" \
    "agent_name=$agent_name" \
    "policy=$policy" \
    "registration_token=$registration_token" 2>/dev/null)"
  if [[ -z "$resp" ]] || printf '%s' "$resp" | ags_mcp_has_error; then
    ags_mcp_call "set_contact_policy" \
      "project_key=$project_key" \
      "agent_name=$agent_name" \
      "policy=$policy" >/dev/null 2>&1 || true
  fi
}

# Three-valued name availability: a name we cannot check is NOT a free name.
# Prints one of: available | occupied | unknown
#
# whois signals all three through an error channel, so the error TEXT decides:
#   - "Agent '<name>' not found in project ..."   -> available (server answered)
#   - "requires registration_token for agent ..." -> occupied (token-strict
#     server confirming the agent exists but refusing an unauthenticated read)
#   - empty response, transport failure, anything else -> unknown
#
# Previously every error mapped to "does not exist", so an auth error or a
# timeout read as "this name is free" and a fresh session could register under
# a live agent's identity. Availability decisions must be fail-closed.
ags_agent_name_status() {
  local project_key="$1" agent_name="$2" response
  response="$(ags_mcp_call "whois" "project_key=$project_key" "agent_name=$agent_name" 2>/dev/null || true)"
  if [[ -z "$response" ]]; then
    printf 'unknown\n'
    return 0
  fi
  if printf '%s' "$response" | ags_mcp_has_error; then
    if printf '%s' "$response" | grep -qiE "requires[ _]registration_token|already authenticated"; then
      printf 'occupied\n'
    elif printf '%s' "$response" | grep -qiE "not found|does not exist|no such agent|unknown agent"; then
      printf 'available\n'
    else
      printf 'unknown\n'
    fi
    return 0
  fi
  if [[ -n "$(printf '%s' "$response" | ags_extract_agent_name)" ]]; then
    printf 'occupied\n'
  else
    printf 'available\n'
  fi
}

# Back-compat wrapper: true only for a positively confirmed existing agent.
# Callers deciding whether a name is FREE must use ags_agent_name_available
# instead, so that 'unknown' is never mistaken for 'available'.
ags_agent_exists() {
  local name_status
  name_status="$(ags_agent_name_status "$1" "$2")"
  [[ "$name_status" == "occupied" ]]
}

# Fail-closed availability check: only an explicit 'available' passes.
ags_agent_name_available() {
  local name_status
  name_status="$(ags_agent_name_status "$1" "$2")"
  [[ "$name_status" == "available" ]]
}

ags_pick_scientist_name() {
  local _prefix="${1:-}"
  ags_pick_adjective_scientist_name
}

# Consecutive 'unknown' answers mean the availability check itself is broken
# (server down mid-run, auth wall, timeouts). Handing out a name we could not
# verify is exactly the failure mode this guard exists to prevent, so stop.
AGS_NAME_UNKNOWN_LIMIT="${AGENTSTACK_NAME_UNKNOWN_LIMIT:-3}"

ags_pick_available_agent_name() {
  local project_key="$1" _prefix="$2" preferred_name="${3:-}"
  local attempts="${AGENTSTACK_AGENT_NAME_ATTEMPTS:-75}"
  local adjective scientist candidate i name_status
  local unknowns=0

  if [[ -n "$preferred_name" ]]; then
    if ags_local_agent_name_conflicts "$project_key" "$preferred_name" candidate; then
      echo "agentstack: local identity '$preferred_name' is already owned by another project or session." >&2
      return 1
    fi
    name_status="$(ags_agent_name_status "$project_key" "$preferred_name")"
    if [[ "$name_status" == "available" ]]; then
      ags_note_scientist_used "$preferred_name" || true
      printf '%s\n' "$preferred_name"
      return 0
    fi
    if [[ "$name_status" == "unknown" ]]; then
      echo "agentstack: cannot verify whether '$preferred_name' is free (ORRERY Mail unreachable or refusing whois); refusing to claim it." >&2
      return 1
    fi
  fi

  for ((i = 0; i < attempts; i++)); do
    candidate="$(ags_pick_adjective_scientist_name)" || return 1
    if ags_local_agent_name_conflicts "$project_key" "$candidate" candidate; then
      continue
    fi
    name_status="$(ags_agent_name_status "$project_key" "$candidate")"
    case "$name_status" in
      available)
        # Record only on the claim: the loop discards candidates, and recording
        # those would burn through the roster with surnames nobody is using.
        ags_note_scientist_used "$candidate" || true
        printf '%s\n' "$candidate"; return 0 ;;
      occupied)  unknowns=0 ;;
      *)
        unknowns=$((unknowns + 1))
        if (( unknowns >= AGS_NAME_UNKNOWN_LIMIT )); then
          echo "agentstack: name availability checks failed $unknowns times in a row; refusing to pick a name that may already be in use." >&2
          return 1
        fi
        ;;
    esac
  done

  for ((i = 2; i < attempts + 200; i++)); do
    adjective="$(ags_pick_adjective)" || return 1
    scientist="$(ags_pick_scientist)" || return 1
    candidate="${adjective}-${i}-${scientist}"
    if ags_local_agent_name_conflicts "$project_key" "$candidate" candidate; then
      continue
    fi
    name_status="$(ags_agent_name_status "$project_key" "$candidate")"
    case "$name_status" in
      available)
        # Record only on the claim: the loop discards candidates, and recording
        # those would burn through the roster with surnames nobody is using.
        ags_note_scientist_used "$candidate" || true
        printf '%s\n' "$candidate"; return 0 ;;
      occupied)  unknowns=0 ;;
      *)
        unknowns=$((unknowns + 1))
        if (( unknowns >= AGS_NAME_UNKNOWN_LIMIT )); then
          echo "agentstack: name availability checks failed $unknowns times in a row; refusing to pick a name that may already be in use." >&2
          return 1
        fi
        ;;
    esac
  done

  return 1
}

ags_register_session() {
  local project_key="$1" program="$2" model="$3" prefix="$4" work_dir="$5" requested_name="${6:-}" requested_mode="${7:-reserved}"
  local invocation_transport="${8:-}" ownership_kind="${9:-}"
  AGS_REGISTERED_AGENT_NAME=""
  AGS_REGISTERED_AGENT_ID=""
  AGS_REGISTERED_REGISTRATION_TOKEN=""
  AGS_REQUESTED_AGENT_NAME=""
  AGS_SERVER_RETURNED_AGENT_NAME=""
  AGS_AGENT_NAME_SUBSTITUTED=0
  AGS_REGISTRATION_FAILURE_KIND=""

  local task_description="Agent session in $work_dir"
  case "$program" in
    claude-code) task_description="Claude session in $work_dir" ;;
    codex) task_description="Codex session in $work_dir" ;;
  esac

  # Resolve and validate project ownership before name availability probes,
  # ensure_project, token writes, or any other Mail/local side effect.
  local registration_token="" context_json=""
  if [[ "$requested_mode" == "reserved" && -n "$requested_name" ]]; then
    registration_token="${CHILD_REGISTRATION_TOKEN:-}"
    if [[ -z "$registration_token" ]]; then
      registration_token="$(ags_load_registration_token "$requested_name" 2>/dev/null || true)"
    fi
    [[ -n "$registration_token" ]] || {
      AGS_REGISTRATION_FAILURE_KIND="ownership"
      echo "agentstack: registration token not available for reserved identity '$requested_name'." >&2
      return 1
    }
  fi
  context_json="$(ags_resolve_registration_context "$project_key" "$work_dir" \
    "$requested_name" "$requested_mode" "$registration_token" "$invocation_transport")" \
    || { AGS_REGISTRATION_FAILURE_KIND="context"; return 1; }
  # ags_resolve_registration_context ran in a command substitution, so its
  # exported AGS_* variables belonged to that subshell. Derive from the value
  # it returned; a stale caller variable must never replace validated context.
  project_key="$(ags_registration_context_project_key "$context_json")" \
    || { AGS_REGISTRATION_FAILURE_KIND="context"; return 1; }

  # A legacy/token-only resume has no independent strong provenance. Its
  # namespace/workspace relationship was checked above; authenticate the same
  # owner token with Mail before ensure_project or any local ownership write.
  if [[ "$requested_mode" == "reserved" ]] && \
     ! ags_registration_owner_exists_for_name "$requested_name"; then
    if ! ags_verify_registration_owner_with_mail \
      "$project_key" "$requested_name" "$registration_token"; then
      echo "agentstack: could not authenticate legacy owner '$requested_name' in '$project_key'; relaunch with an explicit project namespace." >&2
      AGS_REGISTRATION_FAILURE_KIND="ownership"
      return 1
    fi
  fi
  [[ -n "$ownership_kind" ]] || {
    if [[ "$requested_mode" == "candidate" ]]; then
      ownership_kind="top-level"
    else
      ownership_kind="preserve"
    fi
  }

  local agent_name="$requested_name"
  if [[ -z "$agent_name" ]]; then
    agent_name="$(ags_pick_available_agent_name "$project_key" "$prefix")" \
      || { AGS_REGISTRATION_FAILURE_KIND="name-selection"; return 1; }
  elif [[ "$requested_mode" == "candidate" ]]; then
    agent_name="$(ags_pick_available_agent_name "$project_key" "$prefix" "$agent_name")" \
      || { AGS_REGISTRATION_FAILURE_KIND="name-selection"; return 1; }
  fi
  AGS_REQUESTED_AGENT_NAME="$agent_name"

  # Mint a fresh owner token only for a name the server positively reports as
  # free. An 'unknown' answer must not mint one: that is how an unverified name
  # used to get claimed on top of a live agent.
  if [[ -z "$registration_token" ]] && ags_agent_name_available "$project_key" "$agent_name"; then
    registration_token="$(ags_generate_registration_token)" || return 1
  fi
  [[ -n "$registration_token" ]] || {
    AGS_REGISTRATION_FAILURE_KIND="name-selection"
    echo "agentstack: no verified owner token is available for '$agent_name'." >&2
    return 1
  }

  if ags_local_agent_name_conflicts "$project_key" "$agent_name" "$requested_mode"; then
    AGS_REGISTRATION_FAILURE_KIND="local-ownership"
    echo "agentstack: local identity '$agent_name' conflicts with another project or session; refusing registration." >&2
    return 1
  fi
  if ! ags_begin_registration_ownership "$context_json" "$agent_name" \
    "$registration_token" "$ownership_kind" "$requested_mode"; then
    AGS_REGISTRATION_FAILURE_KIND="local-ownership"
    echo "agentstack: could not establish local ownership for '$agent_name'; refusing registration." >&2
    return 1
  fi

  local ensure_result
  ensure_result="$(ags_mcp_call "ensure_project" "human_key=$project_key")" || {
    AGS_REGISTRATION_FAILURE_KIND="mail-unavailable"
    ags_release_registration_ownership
    return 1
  }
  if printf '%s' "$ensure_result" | ags_mcp_has_error; then
    AGS_REGISTRATION_FAILURE_KIND="server-refusal"
    ags_release_registration_ownership
    return 1
  fi

  local register_args=(
    "project_key=$project_key"
    "program=$program"
    "model=$model"
    "name=$agent_name"
    "task_description=$task_description"
  )
  [[ -n "$registration_token" ]] && register_args+=("registration_token=$registration_token")

  local result registered registered_token
  result="$(ags_mcp_call "register_agent" "${register_args[@]}")" || {
    AGS_REGISTRATION_FAILURE_KIND="mail-unavailable"
    ags_release_registration_ownership
    return 1
  }
  if printf '%s' "$result" | ags_mcp_has_error; then
    AGS_REGISTRATION_FAILURE_KIND="server-refusal"
    ags_release_registration_ownership
    return 1
  fi
  registered="$(printf '%s' "$result" | ags_extract_agent_name)"
  if [[ -z "$registered" ]]; then
    AGS_REGISTRATION_FAILURE_KIND="server-refusal"
    ags_release_registration_ownership
    return 1
  fi
  AGS_SERVER_RETURNED_AGENT_NAME="$registered"
  if [[ "$registered" != "$agent_name" ]]; then
    AGS_AGENT_NAME_SUBSTITUTED=1
    # A reserved identity already has a parent task, owner token, tmux metadata,
    # and inbox addressed to the requested name. Adopting a replacement here
    # would strand all of those under the old name. Candidate/top-level launch
    # paths may reconcile their not-yet-started tmux session to the read-back,
    # but an existing identity must fail closed.
    if [[ "$requested_mode" == "reserved" ]]; then
      AGS_REGISTRATION_FAILURE_KIND="identity-substitution"
      ags_release_registration_ownership
      return 2
    fi
    if ags_local_agent_name_conflicts "$project_key" "$registered" substitution; then
      AGS_REGISTRATION_FAILURE_KIND="local-ownership"
      ags_release_registration_ownership
      echo "agentstack: server-returned identity '$registered' conflicts with local ownership; refusing to overwrite it." >&2
      return 1
    fi
  fi
  registered_token="$(printf '%s' "$result" | ags_extract_registration_token)"
  if [[ "$requested_mode" == "reserved" && -n "$registered_token" && \
        "$registered_token" != "$registration_token" ]]; then
    AGS_REGISTRATION_FAILURE_KIND="ownership"
    ags_release_registration_ownership
    echo "agentstack: ORRERY Mail returned a different owner token for reserved identity '$agent_name'." >&2
    return 1
  fi
  [[ -n "$registered_token" ]] || registered_token="$registration_token"
  [[ -n "$registered_token" ]] || {
    AGS_REGISTRATION_FAILURE_KIND="server-refusal"
    ags_release_registration_ownership
    return 1
  }
  if ! ags_commit_registration_ownership "$context_json" "$agent_name" \
    "$registered" "$registered_token" "$ownership_kind"; then
    AGS_REGISTRATION_FAILURE_KIND="persistence"
    ags_release_registration_ownership
    return 1
  fi
  if [[ "$registered" != "$agent_name" ]]; then
    ags_record_name_substitution "$registered" "$agent_name" || true
  fi
  CHILD_REGISTRATION_TOKEN="$registered_token"
  AGS_REGISTERED_REGISTRATION_TOKEN="$registered_token"
  export CHILD_REGISTRATION_TOKEN
  ags_apply_contact_policy "$project_key" "$registered" "$registered_token"
  AGS_REGISTERED_AGENT_NAME="$registered"
  AGS_REGISTERED_AGENT_ID="$(printf '%s' "$result" | ags_extract_agent_id)"
  AGS_REGISTRATION_FAILURE_KIND=""
  printf '%s\n' "$registered"
}

ags_start_mail_watcher() {
  local tmux_bin="$1" hooks_dir="$2"
  local watcher_session="${AGENTSTACK_MAIL_WATCHER_SESSION:-mail-watcher}"
  [[ -n "$tmux_bin" && -n "$hooks_dir" && -f "$hooks_dir/watch_agent_mail_signals.sh" ]] || return 0
  # The installer now runs the watcher as a launchd / systemd service. When that
  # (or any other) watcher holds the single-instance lock, a tmux copy would
  # only start, print "duplicate", and exit — so this is a fallback, not the
  # primary path.
  local pidfile="${AGENTSTACK_MAIL_WATCHER_PIDFILE:-${AGENTSTACK_MAIL_WATCHER_LOCK_DIR:-/tmp/orrery-mail-watcher.lock}/watcher.pid}"
  if [[ -f "$pidfile" ]]; then
    local watcher_pid
    watcher_pid="$(head -n 1 "$pidfile" 2>/dev/null | tr -d '[:space:]')"
    if [[ "$watcher_pid" =~ ^[0-9]+$ ]] && kill -0 "$watcher_pid" 2>/dev/null; then
      return 0
    fi
  fi
  if ! "$tmux_bin" has-session -t "$watcher_session" 2>/dev/null; then
    "$tmux_bin" new-session -d -s "$watcher_session" \
      "bash '$hooks_dir/watch_agent_mail_signals.sh'" >/dev/null 2>&1 \
      && echo "info: started mail-watcher" >&2 || true
  fi
}

ags_record_managed_agent() {
  local managed_file="$1" agent_name="$2"
  [[ -n "$managed_file" && -n "$agent_name" ]] || return 0
  mkdir -p "$(dirname "$managed_file")" 2>/dev/null || true
  grep -qxF "$agent_name" "$managed_file" 2>/dev/null || echo "$agent_name" >> "$managed_file"
}

# --- macOS TCC (privacy-protected folder) access guard ------------------------
# When an agent's working directory sits inside a macOS privacy-protected folder
# (~/Desktop, ~/Downloads, ~/Documents by default) AND the agent cannot read
# files there, macOS is denying access based on the *terminal identity* this
# process inherited — not on file permissions. On a delegate chain that identity
# propagates from the ancestor that launched the ROOT agent: if that ancestor ran
# in a terminal WITHOUT Full Disk Access (e.g. Terminal.app), every descendant
# inherits it (via the env carried into `tmux new-session`) and hits a bare
# `EPERM` ("Operation not permitted") that is almost impossible to diagnose,
# because the tmux server may be owned by an FDA terminal (e.g. Ghostty) while an
# individual pane still carries the non-FDA identity.
#
# This is a DETECT-AND-WARN guard only: it never blocks a spawn and it fires only
# on an actual read failure inside a protected folder (a functional probe → zero
# false positives when access works). Override the protected-dir list with
# AGENTSTACK_TCC_DIRS (colon-separated; legacy whitespace lists also work) or
# disable with AGENTSTACK_TCC_GUARD=0.
ags_tcc_dir_is_protected() {
  local dir="$1" p
  local dirs="${AGENTSTACK_TCC_DIRS:-$HOME/Desktop:$HOME/Downloads:$HOME/Documents}"
  if [[ "$dirs" == *:* ]]; then
    local IFS=:
    for p in $dirs; do
      [[ -n "$p" ]] || continue
      [[ "$dir" == "$p" || "$dir" == "$p"/* ]] && return 0
    done
    return 1
  fi
  # Compatibility with pre-0.9 examples that used a whitespace list.
  for p in $dirs; do
    [[ -n "$p" ]] || continue
    [[ "$dir" == "$p" || "$dir" == "$p"/* ]] && return 0
  done
  return 1
}

ags_warn_tcc_access() {
  local dir="$1" probe
  [[ "${AGENTSTACK_TCC_GUARD:-1}" != "0" ]] || return 0
  [[ "$(uname -s 2>/dev/null)" == "Darwin" ]] || return 0
  [[ -n "$dir" && -d "$dir" ]] || return 0
  ags_tcc_dir_is_protected "$dir" || return 0
  # Functional probe: read one byte from the first regular file in the dir.
  probe="$(find "$dir" -maxdepth 1 -type f 2>/dev/null | head -1)"
  [[ -n "$probe" ]] || return 0
  head -c 1 "$probe" >/dev/null 2>&1 && return 0   # readable → no TCC problem
  {
    printf '\n⚠️  agentstack: cannot read files under a macOS privacy-protected folder:\n'
    printf '      %s\n' "$dir"
    printf '    This agent inherited a terminal identity WITHOUT access to that folder\n'
    printf '    (typically a non-Full-Disk-Access terminal such as Terminal.app somewhere up\n'
    printf '    the launch chain). Files here will fail with "Operation not permitted" (EPERM).\n'
    printf '    Fix (either one):\n'
    printf '      • Relaunch the ROOT agent from a Full-Disk-Access terminal (e.g. Ghostty), or\n'
    printf '      • Move the project outside ~/Desktop, ~/Downloads, ~/Documents.\n'
    printf "    A running agent's access context cannot be changed in place — recreate the session.\n\n"
  } >&2
  return 0
}
