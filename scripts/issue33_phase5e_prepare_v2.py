from __future__ import annotations

from pathlib import Path
import sys

source = Path(__file__).with_name("issue33_phase5e_prepare.py")
builder = source.read_text(encoding="utf-8")

start_marker = "# If legacy state persistence fails after strong-owner publication"
end_marker = "# Replace the hand-written failure cleanup"
start = builder.index(start_marker)
end = builder.index(end_marker, start)

replacement = r'''# If legacy state persistence fails after strong-owner publication, run the
# same validated cleanup path rather than deleting name-keyed files directly.
adopt_start = text.index(
    'if ! CHILD_TOKEN_FILE="$(\n',
    text.index('# Adopt the token the server persisted'),
)
adopt_end_marker = '\nfi\n\n# --- 失敗時cleanup trap ---'
adopt_end = text.index(adopt_end_marker, adopt_start) + len('\nfi\n')
adopt = text[adopt_start:adopt_end]
needle = '''    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"\n    echo "Error: failed to persist the registered child token" >&2\n'''
patched = '''    rm -f "$DIRECT_ONE_SHOT_TOKEN_FILE"\n    (cd "$WORK_DIR" && CHILD_REGISTRATION_TOKEN="$DIRECT_EFFECTIVE_TOKEN" \\\n        /bin/bash "$HOOKS_DIR/cleanup-child-agent.sh" "$CHILD_NAME") >/dev/null 2>&1 || true\n    echo "Error: failed to persist the registered child token" >&2\n'''
if adopt.count(needle) != 1:
    raise SystemExit(f"adopt cleanup anchor count={adopt.count(needle)}")
adopt = adopt.replace(needle, patched, 1)
text = text[:adopt_start] + adopt + text[adopt_end:]

'''

patched_builder = builder[:start] + replacement + builder[end:]
sys.argv = [str(source), *sys.argv[1:]]
exec(compile(patched_builder, str(source), "exec"), {"__name__": "__main__", "__file__": str(source)})
