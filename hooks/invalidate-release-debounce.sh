#!/bin/bash
# PreToolUse(file reservation tools): invalidate sleeping release workers for
# paths being reserved again. Without this, an older Edit worker can release a
# brand-new reservation because the server release operation is path-based.
# Never blocks the reservation tool.

HOOKS_DIR="${AGENTSTACK_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUNTIME_DIR="${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}"
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/reservation-common.sh"

TOOL_INPUT=$(cat)
STATE_DIR="$RUNTIME_DIR/file_release_debounce"
[ -d "$STATE_DIR" ] || exit 0

reservation_extract_session_id "$TOOL_INPUT" || exit 0
AGENT_RESULT="$(resolve_agent_name)"
AGENT_SRC="${AGENT_RESULT%%|*}"
AGENT="${AGENT_RESULT#*|}"
[ "$AGENT_SRC" != "identity-conflict" ] || exit 0
[ -n "$AGENT" ] || exit 0

QUERY_DOCUMENT="$TOOL_INPUT" QUERY_STATE_DIR="$STATE_DIR" \
    QUERY_AGENT="$AGENT" QUERY_ROOTS="$PROTECTED_ROOTS" QUERY_CWD="$RESERVATION_WORK_DIR" \
    QUERY_PROJECT_KEY="$RESERVATION_PROJECT_KEY" \
    QUERY_HOME="$HOME" python3 - <<'PY' >/dev/null 2>&1 || true
import hashlib
import json
import os
import unicodedata
from pathlib import Path

try:
    document = json.loads(os.environ["QUERY_DOCUMENT"])
except Exception:
    raise SystemExit(0)
tool_input = document.get("tool_input") or {}
raw_paths = tool_input.get("paths") or []
if isinstance(raw_paths, str):
    try:
        raw_paths = json.loads(raw_paths)
    except Exception:
        raw_paths = [raw_paths]
if not isinstance(raw_paths, list):
    raise SystemExit(0)

home = os.environ["QUERY_HOME"]
cwd = os.environ["QUERY_CWD"]
# Roots are the physical, already validated roots of this workspace.
roots = [root for root in os.environ.get("QUERY_ROOTS", "").split(":") if root]

agent = os.environ["QUERY_AGENT"]
project_key = os.environ["QUERY_PROJECT_KEY"]
state_dir = Path(os.environ["QUERY_STATE_DIR"])
for raw_path in raw_paths:
    if not isinstance(raw_path, str) or not raw_path:
        continue
    if raw_path.startswith("/"):
        candidates = [raw_path]
    elif raw_path.startswith("~/"):
        candidates = [os.path.join(home, raw_path[2:])]
    else:
        # Reservation tool paths are project-relative. Also try cwd-relative to
        # mirror the Edit guard exactly; the two strings can differ on macOS
        # when /var and /private/var name the same temporary directory.
        candidates = [os.path.join(root, raw_path) for root in roots]
        candidates.append(os.path.join(cwd, raw_path))
    for candidate in candidates:
        # Same normalization as the Edit guard: a symlink or ".." spelling
        # cannot name a slot for a file that lives outside these roots.
        absolute = os.path.realpath(candidate)
        for root in roots:
            try:
                if os.path.commonpath([absolute, root]) != root:
                    continue
            except ValueError:
                continue
            relative = os.path.relpath(absolute, root)
            if relative == ".":
                relative = os.path.basename(absolute)
            normalized = unicodedata.normalize("NFC", relative)
            # Slots are per project namespace (see reservation_debounce_key).
            key = "\0".join((project_key, agent, normalized))
            names = {hashlib.sha1(key.encode("utf-8")).hexdigest()}
            # Workers armed before the namespace was part of the slot used
            # agent+path only, in NFC or (older still) NFD. Invalidating them
            # can only cancel a stale release, never release anything.
            for form in ("NFC", "NFD"):
                legacy = agent + "\0" + unicodedata.normalize(form, relative)
                names.add(hashlib.sha1(legacy.encode("utf-8")).hexdigest())
            for name in names:
                try:
                    (state_dir / name).unlink()
                except OSError:
                    pass
PY

exit 0
