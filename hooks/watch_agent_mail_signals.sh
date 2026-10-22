#!/usr/bin/env bash
# watch_agent_mail_signals.sh - Watch ORRERY Mail signal files and inject
# prompts into the target agent's tmux session.
#
# Uses macOS fswatch (or fallback polling) to monitor signal files.
# When a signal file is created/updated, reads the JSON metadata and
# sends a notification prompt to the agent's Claude Code tmux session.
#
# Usage:
#   bash ~/.agentstack/hooks/watch_agent_mail_signals.sh &
#   # Or run in a dedicated tmux session:
#   tmux new-session -d -s mail-watcher 'bash ~/.agentstack/hooks/watch_agent_mail_signals.sh'

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# The registration library supplies the core project validator, the private
# token reader and the whois check used to prove a recipient. Without it no
# recipient can be proven, so every signal stays pending.
REGISTER_LIB="${AGENTSTACK_REGISTER_LIB:-$SCRIPT_DIR/../bin/lib/agentstack-register.sh}"
if [[ -f "$REGISTER_LIB" ]]; then
    # shellcheck disable=SC1090
    . "$REGISTER_LIB"
fi
MCP_URL="${AGENTSTACK_MCP_URL:-${MCP_URL:-http://127.0.0.1:18765/mcp}}"
# The watcher's own project selection is never evidence about a recipient.
WATCHER_CONTEXT_VARS="AGENTSTACK_PROJECT_KEY PROJECT_KEY AGENTSTACK_PROJECT_REPOSITORY AGENTSTACK_PROJECT_WORK_DIR AGENTSTACK_PROJECT_WORKTREE_ROOT AGENTSTACK_PROTECTED_ROOTS AGENTSTACK_PROJECT_CONTEXT AGENTSTACK_LOOKUP_PROJECT_KEY CHILD_REGISTRATION_TOKEN"

MAIL_HOME="${AGENTSTACK_MAIL_HOME:-$HOME/.agentstack/mail}"
SIGNALS_DIR="${AGENTSTACK_SIGNALS_DIR:-$MAIL_HOME/signals}"
POLL_INTERVAL=2  # seconds (fallback if fswatch unavailable)
WATCHER_LOCK_DIR="${AGENTSTACK_MAIL_WATCHER_LOCK_DIR:-/tmp/orrery-mail-watcher.lock}"
WATCHER_PIDFILE="${AGENTSTACK_MAIL_WATCHER_PIDFILE:-${WATCHER_LOCK_DIR}/watcher.pid}"
WATCHER_HEARTBEAT="${AGENTSTACK_MAIL_WATCHER_HEARTBEAT:-${WATCHER_LOCK_DIR}/heartbeat}"
WATCH_FIFO=""
WATCH_BACKEND_PID=""
LOCK_ACQUIRED=0

# 通知として割り込ませる下限。low|normal|high|urgent。既定 low = 従来どおり全通。
NOTIFY_MIN_IMPORTANCE="${AGENTSTACK_MAIL_NOTIFY_MIN_IMPORTANCE:-low}"

# importance を順序に写す。未知の値は normal 扱い: ORRERY Mail は importance を
# 自由文字列として受けるので、知らない語を落とすと配送が黙って止まる。
importance_rank() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        low) printf '0' ;;
        high) printf '2' ;;
        urgent) printf '3' ;;
        *) printf '1' ;;
    esac
}

importance_at_least() {
    [ "$(importance_rank "$1")" -ge "$(importance_rank "$2")" ]
}

# 2026-05-20 SilverEuler 設計の non-destructive 通知パイプライン:
#   - signal file は server-owned dirty bit (rename/delete しない)
#   - notify-state.json で「(agent, msg_id) → 配送結果」を永続キャッシュ
#   - notify-locks/ で短命 lease lock (重複 inject 防止、watcher/daemon dual で必須)
#   - 失敗時は state に記録するだけで signal は残し、後で再試行可能
STATE_DIR="${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
STATE_FILE="${STATE_DIR}/notify-state.json"
LEASE_DIR="${STATE_DIR}/notify-locks"
SCAN_INTERVAL=30      # periodic scan で取りこぼし救済
RETRY_COOLDOWN=30     # 同一 (agent, msg) の再試行間隔
LEASE_TTL=120         # lease 失効時間（古い lease は強制取り直し）
# 2026-05-22 JollyTesla hang 根治: tmux 呼び出しは server stall 時に同期ブロック
# し、単一スレッドの本体ループ全体を凍結させる (本日 game2 で2回 hang)。全 tmux
# 呼び出しを run_to で時間制限し、配送本体は background worker に切り離す。
TMUX_TIMEOUT="${TMUX_TIMEOUT:-5}"   # tmux 1 コールの上限秒
MAX_WORKERS="${MAX_WORKERS:-12}"    # 同時 delivery worker 上限 (server stall 時の暴走防止)
mkdir -p "$STATE_DIR" "$LEASE_DIR"

log() { echo "[mail-watcher $(date '+%H:%M:%S')] $*"; }

# run_to <secs> <cmd...> : cmd を最大 secs 秒で実行。超過したら TERM→KILL で
# 強制終了し非ゼロを返す。macOS には timeout(1)/gtimeout が無く、watcher は
# bash 3.2 で動くため、background + watchdog で自前実装する。これにより
# tmux のブロックが本体ループへ波及しなくなる。
run_to() {
    local secs="$1"; shift
    "$@" &
    local cmd_pid=$!
    # watchdog: fd を /dev/null へ向ける ($(run_to ...) が watchdog の stdout 保持で
    # ハングしないように)。cmd 側は呼び出し元の stdout を保持し続ける。
    ( sleep "$secs"; kill -TERM "$cmd_pid" 2>/dev/null; sleep 0.3; kill -KILL "$cmd_pid" 2>/dev/null ) >/dev/null 2>&1 &
    local wd_pid=$!
    local rc=0
    wait "$cmd_pid" 2>/dev/null || rc=$?
    kill "$wd_pid" 2>/dev/null || true
    wait "$wd_pid" 2>/dev/null || true
    return "$rc"
}

read_signal_meta() {
    python3 - "$1" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}
msg = data.get("message") or {}
st = os.stat(path)
print(msg.get("id") or "")
print(msg.get("from") or "unknown")
print((msg.get("subject") or "(no subject)")[:80])
print(msg.get("importance") or "normal")
print(int(st.st_mtime))
# A short body is included by newer mail servers. Keep it on one line before
# passing it to tmux: an embedded newline would submit an incomplete prompt.
snippet = (msg.get("body_snippet") or "").replace("\r", " ").replace("\n", " ⏎ ")
print(snippet[:500])
print("1" if msg.get("body_truncated") else "0")
for field in ("project_key", "project"):
    value = data.get(field)
    print(value if isinstance(value, str) and "\n" not in value else "")
PY
}

# Canonical project of a legacy slug-only signal, from Mail's own project
# resource. The answer must name the requested slug and carry a human_key;
# anything else leaves the signal without a project (and so pending).
resolve_signal_project_key() {
    local slug="$1"
    [[ -n "$slug" ]] || return 1
    declare -F ags_mail_load_token >/dev/null 2>&1 && ags_mail_load_token
    RESOURCE_SLUG="$slug" RESOURCE_URL="$MCP_URL" python3 - <<'PY' 2>/dev/null
import json
import os
import urllib.request

slug = os.environ["RESOURCE_SLUG"]
payload = json.dumps({
    "jsonrpc": "2.0",
    "id": "watcher-project",
    "method": "resources/read",
    "params": {"uri": f"resource://project/{slug}"},
}).encode()
headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
bearer = os.environ.get("MCP_AGENT_MAIL_TOKEN", "").strip()
if bearer:
    headers["Authorization"] = "Bearer " + bearer
request = urllib.request.Request(os.environ["RESOURCE_URL"], data=payload, headers=headers)
with urllib.request.urlopen(request, timeout=5) as response:
    raw = response.read().decode("utf-8", "replace")
for line in raw.splitlines():
    if line.startswith("data:"):
        raw = line[5:].strip()
        break
body = json.loads(raw)
contents = (body.get("result") or {}).get("contents") or []
if len(contents) != 1:
    raise SystemExit(1)
project = json.loads(contents[0].get("text") or "null")
if not isinstance(project, dict) or project.get("slug") != slug:
    raise SystemExit(1)
human_key = project.get("human_key")
if not isinstance(human_key, str) or not human_key or "\n" in human_key:
    raise SystemExit(1)
print(human_key)
PY
}

# State and lease entries are per (project, agent, message). The key is a JSON
# triple (and its hash for lease directories), so separators inside a logical
# project key cannot make two deliveries share an entry.
delivery_key() {
    python3 -c 'import hashlib, json, sys
triple = json.dumps(sys.argv[1:4], ensure_ascii=False)
print(hashlib.sha256(triple.encode("utf-8")).hexdigest() if sys.argv[4] == "hash" else triple)' "$1" "$2" "$3" "$4"
}

state_should_attempt() {
    python3 - "$STATE_FILE" "$1" "$2" "$3" "$RETRY_COOLDOWN" <<'PY'
import json, sys, time
path, project, agent, msg_key = sys.argv[1:5]
cooldown = int(sys.argv[5])
compound = json.dumps([project, agent, msg_key], ensure_ascii=False)
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}
entry = data.get(compound)
if not entry:
    sys.exit(0)
if entry.get("last_result") == "success":
    sys.exit(1)
last_attempt = int(entry.get("last_attempt_epoch", 0) or 0)
if int(time.time()) - last_attempt >= cooldown:
    sys.exit(0)
sys.exit(1)
PY
}

state_mark_result() {
    python3 - "$STATE_FILE" "$1" "$2" "$3" "$4" "$5" <<'PY'
import fcntl, json, sys, time
from pathlib import Path
path, project, agent, msg_key, result, source = sys.argv[1:7]
compound = json.dumps([project, agent, msg_key], ensure_ascii=False)
p = Path(path)
p.parent.mkdir(parents=True, exist_ok=True)
# 配送 worker を background 化したため複数プロセスが同時に state を read-modify-
# write する。flock で直列化しないと key の lost-update が起きる (success が消え
# て二重 inject)。lock は同一 fd close / プロセス死で自動解放されるので hang し
# ない。
lock = open(str(p) + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
try:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    now = int(time.time())
    entry = data.get(compound, {})
    entry.update({
        "project_key": project,
        "agent": agent,
        "msg_key": msg_key,
        "last_result": result,
        "last_attempt_epoch": now,
        "last_attempt_ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "source": source,
    })
    if result == "success":
        entry["last_success_epoch"] = now
        entry["last_success_ts"] = entry["last_attempt_ts"]
    data[compound] = entry
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic swap so a reader never sees a half-written file
finally:
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
PY
}

acquire_delivery_lease() {
    local lease_path
    lease_path="${LEASE_DIR}/$(delivery_key "$1" "$2" "$3" hash).lock"
    local now
    now=$(date +%s)

    if mkdir "$lease_path" 2>/dev/null; then
        printf '%s\n' "$now" > "$lease_path/ts"
        return 0
    fi

    local ts="0"
    [[ -f "$lease_path/ts" ]] && ts=$(<"$lease_path/ts")
    if (( now - ts > LEASE_TTL )); then
        rm -rf "$lease_path" 2>/dev/null || true
        if mkdir "$lease_path" 2>/dev/null; then
            printf '%s\n' "$now" > "$lease_path/ts"
            return 0
        fi
    fi
    return 1
}

release_delivery_lease() {
    rm -rf "${LEASE_DIR}/$(delivery_key "$1" "$2" "$3" hash).lock" 2>/dev/null || true
}

# Canonical project of a recipient, from its actual directory and its own
# session context only (run in a subshell with the watcher's variables gone,
# so no field is ever filled from the watcher).
#
# Every marker the session actually carries has to agree with the context
# resolved from the concrete pane's directory: a key alias cannot mask a
# second one, and a valid physical key cannot make an explicit repository,
# work directory, worktree root or protected root from another project
# irrelevant. Aliases are compared canonically, and any worktree of the same
# repository counts as the same workspace. A session with no markers at all
# falls back to its directory's own namespace.
recipient_project() (
    local cwd="$1" selected="$2" alias_key="$3" repository="$4" work_dir="$5"
    local worktree_root="$6" protected_roots="$7"
    local var="" resolved="" root="" old_ifs=""
    for var in $WATCHER_CONTEXT_VARS; do unset "$var"; done
    if [[ -n "$selected" ]]; then
        export AGENTSTACK_PROJECT_KEY="$selected"
        [[ -n "$repository" ]] && export AGENTSTACK_PROJECT_REPOSITORY="$repository"
        [[ -n "$work_dir" ]] && export AGENTSTACK_PROJECT_WORK_DIR="$work_dir"
        ags_child_target_context "$cwd" "$selected" || exit 1
    else
        ags_child_target_context "$cwd" \
            "$(agentstack_context_field "$(agentstack_resolve_invocation_context "$cwd")" project_key)" \
            || exit 1
    fi
    resolved="$AGS_CHILD_PROJECT_KEY"
    # A second key spelling must resolve to the very same project.
    if [[ -n "$alias_key" && "$alias_key" != "$selected" ]]; then
        ( ags_child_target_context "$cwd" "$alias_key" \
            && [[ "$AGS_CHILD_PROJECT_KEY" == "$resolved" ]] ) || exit 1
    fi
    if [[ -n "$repository" ]]; then
        [[ "$(agentstack_physical_dir "$repository" 2>/dev/null)" == "$AGS_CHILD_REPOSITORY" ]] || exit 1
    fi
    # A recorded workspace may be another worktree of the same repository, but
    # it must belong to this very project.
    for root in "$work_dir" "$worktree_root"; do
        [[ -n "$root" ]] || continue
        ( ags_child_target_context "$root" "$resolved" \
            && [[ "$AGS_CHILD_PROJECT_KEY" == "$resolved" ]] ) || exit 1
    done
    if [[ -n "$protected_roots" ]]; then
        old_ifs="$IFS"
        IFS=":"
        for root in $protected_roots; do
            IFS="$old_ifs"
            [[ -n "$root" ]] || continue
            ( ags_child_target_context "$root" "$resolved" \
                && [[ "$AGS_CHILD_PROJECT_KEY" == "$resolved" ]] ) || exit 1
        done
        IFS="$old_ifs"
    fi
    printf '%s' "$resolved"
)

# The concrete pane's own identity as one tab-separated line: its pane id, the
# id of the session it belongs to, that session's current name, and its own
# directory. Fails unless the pane is still this exact pane, still in a
# session, and still named for the intended agent. Every step of the evidence
# re-takes this line and compares it, so a session renamed or a name rebound to
# another session between two calls is a failure rather than a silent swap.
pane_identity() {
    local pane="$1" agent="$2" line="" _pane_id="" _session_id="" name="" cwd=""
    line="$(run_to "$TMUX_TIMEOUT" tmux display-message -p -t "$pane" \
        '#{pane_id}	#{session_id}	#{session_name}	#{pane_current_path}' 2>/dev/null)" || return 1
    IFS=$'\t' read -r _pane_id _session_id name cwd <<< "$line"
    [[ "$_pane_id" == "$pane" && -n "$_session_id" && "$name" == "$agent" && -n "$cwd" ]] || return 1
    printf '%s' "$line"
}

# Everything that makes PANE the proven recipient of AGENT in PROJECT, printed
# as one line (the token appears only as a digest). Fails when the pane is not
# the exact named session's pane, its own session context does not validate
# for its actual directory, the canonical project differs from the signal's,
# or the agent's private token is missing, unsafe, or not accepted by Mail
# right now (this is re-proved at every call, including the rechecks).
# The watcher's own project variables are removed first, so only the
# recipient's session and directory count.
recipient_evidence() {
    local agent="$1" pane="$2" project="$3"
    local pane_line="" name="" cwd="" var="" snapshot="" final_snapshot="" line=""
    local selected="" resolved=""
    local token_file="" token=""
    local _pane_id="" _session_id=""
    local session_key="" session_project="" session_repository="" session_work_dir=""
    local session_worktree_root="" session_protected_roots=""
    declare -F ags_child_target_context >/dev/null 2>&1 || return 1
    declare -F ags_read_private_child_token >/dev/null 2>&1 || return 1
    pane_line="$(pane_identity "$pane" "$agent")" || return 1
    IFS=$'\t' read -r _pane_id _session_id name cwd <<< "$pane_line"
    # One complete snapshot of that one session, addressed by the session id the
    # pane itself just reported. Reading by the agent's name instead would let a
    # session renamed after the pane was resolved answer for this pane. A
    # failed, timed-out or killed read is a failure of the whole proof: it must
    # never be mistaken for a session that simply carries no markers.
    snapshot="$(run_to "$TMUX_TIMEOUT" tmux show-environment -t "$_session_id" 2>/dev/null)" || return 1
    # tmux prints NAME=value for a set variable and -NAME for an unset one.
    # Only these six names are read, matched literally as whole entries; nothing
    # is sourced, evaluated or exported, and any other line is ignored. Each
    # marker keeps its own variable: packing them into one delimited string and
    # reading it back would collapse the empty ones, because bash treats a tab
    # as IFS whitespace, and an absent optional marker would shift the rest.
    while IFS= read -r line; do
        case "$line" in
            AGENTSTACK_PROJECT_KEY=*) session_key="${line#*=}" ;;
            PROJECT_KEY=*) session_project="${line#*=}" ;;
            AGENTSTACK_PROJECT_REPOSITORY=*) session_repository="${line#*=}" ;;
            AGENTSTACK_PROJECT_WORK_DIR=*) session_work_dir="${line#*=}" ;;
            AGENTSTACK_PROJECT_WORKTREE_ROOT=*) session_worktree_root="${line#*=}" ;;
            AGENTSTACK_PROTECTED_ROOTS=*) session_protected_roots="${line#*=}" ;;
        esac
    done <<< "$snapshot"
    # The markers just read describe a session; this proves it is still the
    # session of this pane, under this name.
    [[ "$(pane_identity "$pane" "$agent")" == "$pane_line" ]] || return 1
    selected="${session_key:-$session_project}"
    resolved="$(recipient_project "$cwd" "$selected" "${session_project:-$session_key}" \
        "$session_repository" "$session_work_dir" "$session_worktree_root" \
        "$session_protected_roots")" || return 1
    [[ -n "$resolved" && "$resolved" == "$project" ]] || return 1
    token_file="$(ags_registration_token_file "$agent")" || return 1
    token="$(ags_read_private_child_token "$token_file" 2>/dev/null)" || return 1
    # Mail is asked every time: an unchanged token file is not evidence that
    # the registration it belonged to still exists.
    ( for var in $WATCHER_CONTEXT_VARS; do unset "$var"; done
      ags_verify_child_credential "$project" "$agent" "$token_file" ) || return 1
    # The proof is a round trip to Mail, and both the pane and the session's own
    # context can move while it is in flight. The markers are read again from
    # the same concrete session and must still be the ones that were validated:
    # a session that only re-pointed its project variables keeps the same pane,
    # id, name and directory, so the identity check alone would not see it.
    # The status is checked before the text: a failed read prints nothing, which
    # is exactly what a successful read of a marker-free session prints.
    final_snapshot="$(run_to "$TMUX_TIMEOUT" tmux show-environment -t "$_session_id" 2>/dev/null)" || return 1
    [[ "$final_snapshot" == "$snapshot" ]] || return 1
    # As after the first snapshot, the identity is taken last, so a rename or a
    # rebinding during that final read is seen before anything is captured.
    [[ "$(pane_identity "$pane" "$agent")" == "$pane_line" ]] || return 1
    # Every marker that was read is part of the evidence, so a later recheck
    # compares all of them, not only the key that was selected from them. An
    # unrelated session variable that moves simply leaves the signal pending.
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$pane_line" "$selected" \
        "$session_key" "$session_project" "$session_repository" "$session_work_dir" \
        "$session_worktree_root" "$session_protected_roots" "$resolved" \
        "$(printf '%s' "$token" | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
}

is_pid_running() {
    local pid="${1:-}"
    [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

write_heartbeat() {
    mkdir -p "$(dirname "$WATCHER_HEARTBEAT")"
    : > "$WATCHER_HEARTBEAT"
}

acquire_lock() {
    local existing_pid=""

    if mkdir "$WATCHER_LOCK_DIR" 2>/dev/null; then
        mkdir -p "$(dirname "$WATCHER_PIDFILE")"
        printf '%s\n' "$$" > "$WATCHER_PIDFILE"
        LOCK_ACQUIRED=1
        write_heartbeat
        return 0
    fi

    if [[ -f "$WATCHER_PIDFILE" ]]; then
        existing_pid=$(<"$WATCHER_PIDFILE")
    fi

    if is_pid_running "$existing_pid"; then
        log "Watcher already running (PID ${existing_pid}); exiting duplicate instance"
        exit 0
    fi

    log "Stale watcher lock detected; taking ownership"
    # A watcher that died without running its EXIT trap leaves the heartbeat
    # (and possibly the pidfile) inside the lock dir. `rmdir` refuses a
    # non-empty dir, the following `mkdir` then fails, and under `set -e` the
    # process exits 1 - which the service manager restarts every few seconds,
    # forever. Clear the whole dir so takeover cannot wedge on leftovers.
    rm -f "$WATCHER_PIDFILE" "$WATCHER_HEARTBEAT"
    rm -rf "$WATCHER_LOCK_DIR"
    mkdir "$WATCHER_LOCK_DIR"
    mkdir -p "$(dirname "$WATCHER_PIDFILE")"
    printf '%s\n' "$$" > "$WATCHER_PIDFILE"
    LOCK_ACQUIRED=1
    write_heartbeat
}

cleanup() {
    if is_pid_running "$WATCH_BACKEND_PID"; then
        kill "$WATCH_BACKEND_PID" 2>/dev/null || true
        kill -KILL "$WATCH_BACKEND_PID" 2>/dev/null || true
    fi
    if [[ -n "$WATCH_FIFO" && -p "$WATCH_FIFO" ]]; then
        rm -f "$WATCH_FIFO"
    fi
    if [[ "$LOCK_ACQUIRED" -eq 1 ]]; then
        rm -f "$WATCHER_PIDFILE" "$WATCHER_HEARTBEAT"
        rmdir "$WATCHER_LOCK_DIR" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM

# Ensure signals directory exists
mkdir -p "$SIGNALS_DIR"
acquire_lock

handle_signal_file() {
    local signal_file="$1"
    local agent_name parent_dir per_msg_file=0

    if [[ -z "$signal_file" || ! -f "$signal_file" ]]; then
        return
    fi

    # Per-message layout: agents/{agent_name}/{msg_id}.signal
    # Legacy layout:      agents/{agent_name}.signal
    parent_dir=$(basename "$(dirname "$signal_file")")
    if [[ "$parent_dir" == "agents" ]]; then
        agent_name=$(basename "$signal_file" .signal)
    else
        agent_name="$parent_dir"
        per_msg_file=1
    fi

    if [[ -z "$agent_name" ]]; then
        return
    fi

    # signal は server-owned dirty bit。client は rename/delete しない。
    # 重複処理防止は state_should_attempt + acquire_delivery_lease で行う。
    # bash 3.2 (macOS system) 互換: mapfile を使わず逐次 read する。
    local msg_id from subject importance mtime body_snippet body_truncated msg_key
    local signal_project_key signal_project_slug
    {
        IFS= read -r msg_id
        IFS= read -r from
        IFS= read -r subject
        IFS= read -r importance
        IFS= read -r mtime
        IFS= read -r body_snippet
        IFS= read -r body_truncated
        IFS= read -r signal_project_key
        IFS= read -r signal_project_slug
    } < <(read_signal_meta "$signal_file")
    msg_id="${msg_id:-}"
    from="${from:-unknown}"
    subject="${subject:-(no subject)}"
    importance="${importance:-normal}"
    mtime="${mtime:-0}"
    body_snippet="${body_snippet:-}"
    body_truncated="${body_truncated:-0}"
    msg_key="${msg_id:-mtime-${mtime}}"
    signal_project_key="${signal_project_key:-}"
    signal_project_slug="${signal_project_slug:-}"
    # A signal without its canonical project (older servers) is delivered only
    # when Mail itself names the project for its slug.
    if [[ -z "$signal_project_key" && -n "$signal_project_slug" ]]; then
        signal_project_key="$(resolve_signal_project_key "$signal_project_slug" || true)"
    fi
    if [[ -z "$signal_project_key" ]]; then
        return 0
    fi

    # `set -e` 下で `|| return` だと return が直前の exit code を継承して
    # 関数が non-zero で抜け、呼び出し元の while ループが止まる。
    # 早期スキップは `return 0` を明示してスクリプト継続を保証する。
    state_should_attempt "$signal_project_key" "$agent_name" "$msg_key" || return 0

    # 割り込みの閾値。既定は low = 従来どおり全部通す。
    #
    # 通知は相手の入力欄に直接タイプされるので、人間が親と会話している最中に子の
    # 進捗報告が挟まる。「子を何体も抱えている親」ほど会話が細切れになる、という
    # 報告がテスターから届いた。ここで落としても**メールは消えない**: signal を
    # 消費しないまま state に記録するだけなので、次に fetch_inbox を呼べば普通に
    # 読める。奪うのは割り込む権利であって、届く権利ではない。
    if ! importance_at_least "$importance" "$NOTIFY_MIN_IMPORTANCE"; then
        state_mark_result "$signal_project_key" "$agent_name" "$msg_key" "below_min_importance" "watcher"
        return 0
    fi

    acquire_delivery_lease "$signal_project_key" "$agent_name" "$msg_key" || return 0

    log "Signal: ${agent_name} ← ${from} [${importance}]: ${subject}"

    # 配送 (tmux 操作) は background worker に切り離す。tmux が server stall で
    # ブロックしても本体ループは即座に次の signal へ進めるため、健全な pane への
    # 配送が止まらない (= hang しない)。worker は run_to で各 tmux 呼び出しを時間
    # 制限し、state 記録 + lease 解放 + signal 削除まで自己完結する。
    # server stall 時の worker 暴走を防ぐため同時数を MAX_WORKERS で制限する。
    # worker は run_to により有限時間で必ず終了するので、この待ちは有界 (最悪
    # TMUX_TIMEOUT 程度) であり恒久 deadlock しない。
    while [ "$(jobs -p 2>/dev/null | wc -l | tr -d ' ')" -ge "$MAX_WORKERS" ]; do
        sleep 0.1
    done
    deliver_worker "$signal_file" "$signal_project_key" "$agent_name" "$msg_key" "$from" "$subject" "$importance" "$per_msg_file" "$body_snippet" "$body_truncated" &
}

# deliver_worker: 1 signal の配送を完結させる background ジョブ。すべての tmux
# 呼び出しを run_to で時間制限するため、ここがブロックしても本体ループには波及
# しない。
#
# tmux session 名 = エージェント名の規約に従い exact match のみ。過去にあった
# 「ペイン text に agent_name が含まれていれば fallback resolve」は、別エージェン
# トのペインに偶然名前が現れた場合に誤配する構造的バグの元 (2026-05-20
# BoldLeeuwenhoek の古い signal が SwiftFaraday へ誤配)。session 不在は誤配より
# 安全な skip として扱う。
deliver_worker() {
    local signal_file="$1" project="$2" agent_name="$3" msg_key="$4"
    local from="$5" subject="$6" importance="$7" per_msg_file="$8"
    local body_snippet="${9:-}" body_truncated="${10:-0}"
    local session_name="$agent_name" pane="" evidence="" current=""

    finish() {
        state_mark_result "$project" "$agent_name" "$msg_key" "$1" "watcher"
        release_delivery_lease "$project" "$agent_name" "$msg_key"
    }

    # Exact session match only ("=name"), then one concrete pane: later calls
    # target that pane, never a name tmux could resolve to something else.
    if ! run_to "$TMUX_TIMEOUT" tmux has-session -t "=$session_name" 2>/dev/null; then
        finish "session_not_found"
        return 0
    fi
    # "=name" is a target-session; a target-pane needs "=name:", the current
    # pane of that exact session. Real tmux answers the bare form with an empty
    # pane id and exit 0 (and refuses it outright for capture-pane), so the
    # wrong grammar looks like an absent recipient rather than an error. The
    # "=" still forces an exact name, so a longer session that merely starts
    # with this name cannot answer.
    pane="$(run_to "$TMUX_TIMEOUT" tmux display-message -p -t "=$session_name:" '#{pane_id}' 2>/dev/null || true)"
    if [[ ! "$pane" =~ ^%[0-9]+$ ]]; then
        finish "session_not_found"
        return 0
    fi

    # Nothing is read from or typed into the pane until it is proven to be this
    # agent in the signal's project; otherwise the signal stays pending.
    if ! evidence="$(recipient_evidence "$agent_name" "$pane" "$project")"; then
        finish "recipient_unverified"
        return 0
    fi

    # bare shell には inject しない (Claude REPL でないため)。
    # busy 判定はあえて行わない: 2026-05-22 に busy-skip を入れたところ、busy な
    # agent (特に game 進行役の NavyMaxwell は "Running scheduled task" 等で常時
    # busy 表示) へ通知が届かず game が止まる副作用が出た。hang は run_to timeout +
    # worker 切り離しで構造的に防げており、busy pane への send-keys はもう安全
    # (Claude が input をキューし、ターン完了後に処理する = むしろ望ましい挙動)。
    # したがって busy でも inject する (旧 watcher と同じ配送方針に戻す)。
    local last_lines rc=0
    last_lines=$(run_to "$TMUX_TIMEOUT" tmux capture-pane -t "$pane" -p -S -5 2>/dev/null) || rc=$?
    if [ "$rc" -ne 0 ]; then
        # capture が時間内に返らない = server stall。inject せず後で再試行。
        finish "capture_timeout"
        return 0
    fi
    if echo "$last_lines" | grep -qE '(\$ ?$|% ?$)' && \
       ! echo "$last_lines" | grep -qE '(❯|Claude|claude|ctx:|Sonnet|Opus|Haiku|›)'; then
        finish "bare_shell"
        return 0
    fi

    local prompt
    if [[ -n "$body_snippet" && "$body_truncated" == "0" ]]; then
        prompt="ORRERY Mail notification: message from ${from} [${importance}]: ${subject}. Body (complete; no inbox fetch needed): ${body_snippet}"
    elif [[ -n "$body_snippet" ]]; then
        prompt="ORRERY Mail notification: message from ${from} [${importance}]: ${subject}. Body preview: ${body_snippet} ... Fetch inbox to read the rest."
    else
        prompt="ORRERY Mail notification: message from ${from} [${importance}]: ${subject}. Please call fetch_inbox to read it."
    fi

    # The same pane, session, directory, session context and token must still
    # hold right before each write. This narrows, but cannot close, the window
    # in which tmux could hand the pane to something else.
    current="$(recipient_evidence "$agent_name" "$pane" "$project" || true)"
    if [[ "$current" != "$evidence" ]]; then
        finish "recipient_changed"
        return 0
    fi
    if ! run_to "$TMUX_TIMEOUT" tmux send-keys -t "$pane" -l "$prompt" 2>/dev/null; then
        finish "inject_failed"
        return 0
    fi
    sleep 0.2
    current="$(recipient_evidence "$agent_name" "$pane" "$project" || true)"
    if [[ "$current" != "$evidence" ]]; then
        # The text is already typed, but it is not submitted into a pane that
        # is no longer this agent's; the signal stays pending.
        finish "recipient_changed"
        return 0
    fi
    # submit は Enter keysym ではなく C-m（Ctrl+M=CR）を使う。spawn_child.sh が
    # Claude/Codex 両方の prompt 注入で C-m を使っており（proven-universal）、Codex
    # REPL では Enter が submit されないことがある（2026-06-05 WildCurie が Enter で
    # 固まった件）。Claude Code は Enter/C-m 両方 submit するので C-m に統一しても無回帰
    # （捨て子 Claude で実測確認）。
    if ! run_to "$TMUX_TIMEOUT" tmux send-keys -t "$pane" C-m 2>/dev/null; then
        finish "submit_failed"
        return 0
    fi

    state_mark_result "$project" "$agent_name" "$msg_key" "success" "watcher"
    release_delivery_lease "$project" "$agent_name" "$msg_key"
    log "  Injected notification into '$agent_name' (pane: $pane)"
    # Per-message files are watcher-owned (each represents one delivery); unlink
    # them on success so identical msg_ids never re-fire. Legacy single-file
    # signals remain server-owned and are cleared by fetch_inbox.
    if (( per_msg_file == 1 )); then
        rm -f "$signal_file" 2>/dev/null || true
        # Try to remove the per-agent dir if it's now empty (best-effort).
        rmdir "$(dirname "$signal_file")" 2>/dev/null || true
    fi
    return 0
}

process_existing_signals() {
    # Match both legacy (agents/{name}.signal) and per-message
    # (agents/{name}/{msg_id}.signal) layouts. find -name globs file names so
    # both layouts surface here; handle_signal_file disambiguates by parent dir.
    while IFS= read -r -d '' signal_file; do
        write_heartbeat
        handle_signal_file "$signal_file"
    done < <(find "$SIGNALS_DIR" -name "*.signal" -type f -print0 2>/dev/null)
}

if command -v fswatch &>/dev/null; then
    log "Starting fswatch on $SIGNALS_DIR"
    process_existing_signals
    WATCH_FIFO="$(mktemp -u "/tmp/orrery-mail-fswatch.XXXXXX")"
    mkfifo "$WATCH_FIFO"
    fswatch -r --event Created --event Updated "$SIGNALS_DIR" > "$WATCH_FIFO" &
    WATCH_BACKEND_PID=$!
    # fswatch + 30 秒ごとの periodic scan の二段構え。fswatch イベント
    # 取りこぼし時の救済 + state cooldown 経過後の再試行を担う。
    exec 3<>"$WATCH_FIFO"
    last_scan=$(date +%s)
    while true; do
        write_heartbeat
        if read -r -t 1 filepath <&3; then
            if [[ "$filepath" == *.signal ]]; then
                sleep 0.1
                handle_signal_file "$filepath"
            fi
        fi
        now=$(date +%s)
        if (( now - last_scan >= SCAN_INTERVAL )); then
            process_existing_signals
            last_scan=$now
        fi
    done
else
    log "fswatch not found, using polling (${POLL_INTERVAL}s interval)"
    while true; do
        write_heartbeat
        process_existing_signals
        sleep "$POLL_INTERVAL"
    done
fi
