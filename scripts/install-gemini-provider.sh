#!/usr/bin/env bash
# Install the fork-only provider runtime and Google Antigravity / Gemini adapter
# into an existing ORRERY installation.
set -euo pipefail

PROG="install-gemini-provider.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${AGENTSTACK_HOME:-$HOME/.agentstack}"
DRY_RUN=false
CONFIGURE_MCP=false
PYTHON_BIN="${AGENTSTACK_PYTHON:-python3}"

usage() {
  cat >&2 <<'EOF'
Usage: scripts/install-gemini-provider.sh [options]

Options:
  --install-dir PATH   AgentStack install dir (default ~/.agentstack)
  --configure-mcp      also add/update the global Antigravity ORRERY Mail MCP entry
  --dry-run            print planned copies only
  -h, --help           show this help

The script never edits shell dotfiles and never changes Antigravity permission
settings. MCP configuration is changed only when --configure-mcp is explicit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      INSTALL_DIR="$2"; shift 2 ;;
    --configure-mcp)
      CONFIGURE_MCP=true; shift ;;
    --dry-run)
      DRY_RUN=true; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "$PROG: unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

INSTALLED_SERVER="$INSTALL_DIR/dashboard/server.py"
INSTALLED_CORE="$INSTALL_DIR/dashboard/server_core.py"
MANIFEST="$INSTALL_DIR/install-state.json"
if [[ ! -f "$INSTALLED_SERVER" ]]; then
  echo "$PROG: existing dashboard/server.py not found under $INSTALL_DIR; install ORRERY core first" >&2
  exit 1
fi
if [[ ! -f "$MANIFEST" ]]; then
  echo "$PROG: existing install-state.json not found under $INSTALL_DIR; install ORRERY core first" >&2
  exit 1
fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || [[ -x "$PYTHON_BIN" ]] || {
  echo "$PROG: selected Python is unavailable: $PYTHON_BIN" >&2
  exit 1
}

# Retrofitting a provider must not replace the installed control-plane snapshot
# with the checkout's 5k-line server. Preserve the version the operator is
# actually running, then put only the thin provider-aware entry point in front.
# On repeated provider installs the entry point is already ours, so keep the
# previously preserved core. If a later ORRERY core install replaced server.py,
# it will no longer match this wrapper marker and the fresh core is preserved.
if grep -q 'provider_runtime' "$INSTALLED_SERVER" 2>/dev/null && \
   grep -q 'server_core' "$INSTALLED_SERVER" 2>/dev/null; then
  if [[ ! -f "$INSTALLED_CORE" ]]; then
    echo "$PROG: provider wrapper is installed but dashboard/server_core.py is missing" >&2
    exit 1
  fi
  echo "$PROG: reuse preserved dashboard/server_core.py"
else
  echo "$PROG: preserve dashboard/server.py -> dashboard/server_core.py"
  if [[ "$DRY_RUN" != true ]]; then
    cp "$INSTALLED_SERVER" "$INSTALLED_CORE"
    chmod 644 "$INSTALLED_CORE"
  fi
fi

FILES=(
  "bin/agent-start-gemini"
  "bin/agentstack-gemini-bootstrap"
  "bin/agentstack-gemini-setup"
  "bin/agentstack-gemini-child-mail"
  "bin/agentstack-gemini-stream"
  "hooks/spawn_gemini_child.sh"
  "hooks/spawn_gemini_preregistered.sh"
  "dashboard/server.py"
  "dashboard/provider_runtime.py"
  "dashboard/provider_classification.py"
  "dashboard/provider_launch_tracking.py"
  "dashboard/providers/registry.py"
  "dashboard/assets/google.svg"
)

for relative in "${FILES[@]}"; do
  src="$REPO_ROOT/$relative"
  dst="$INSTALL_DIR/$relative"
  [[ -f "$src" ]] || { echo "$PROG: missing source file: $src" >&2; exit 1; }
  echo "$PROG: copy $relative -> $dst"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    case "$relative" in
      bin/*|hooks/*|dashboard/server.py)
        chmod 755 "$dst"
        ;;
      *)
        chmod 644 "$dst"
        ;;
    esac
  fi
done

echo "$PROG: record provider payload ownership -> $MANIFEST"
if [[ "$DRY_RUN" != true ]]; then
  "$PYTHON_BIN" - "$MANIFEST" "$INSTALL_DIR" "${FILES[@]}" "dashboard/server_core.py" <<'PY'
import json
import os
import pathlib
import sys

manifest = pathlib.Path(sys.argv[1]).expanduser()
install_dir = pathlib.Path(sys.argv[2]).expanduser().resolve(strict=False)
relative_paths = sys.argv[3:]
try:
    data = json.loads(manifest.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid core install manifest {manifest}: {exc}")
if not isinstance(data, dict):
    raise SystemExit(f"invalid core install manifest {manifest}: expected object")
owned_files = data.get("owned_files")
owned_dirs = data.get("owned_dirs")
if not isinstance(owned_files, list) or not all(isinstance(item, str) for item in owned_files):
    raise SystemExit(f"invalid core install manifest {manifest}: owned_files must be a string list")
if not isinstance(owned_dirs, list) or not all(isinstance(item, str) for item in owned_dirs):
    raise SystemExit(f"invalid core install manifest {manifest}: owned_dirs must be a string list")

files = set(owned_files)
dirs = set(owned_dirs)
for relative in relative_paths:
    rel = pathlib.PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise SystemExit(f"unsafe provider payload path: {relative}")
    path = (install_dir / pathlib.Path(*rel.parts)).resolve(strict=False)
    try:
        path.relative_to(install_dir)
    except ValueError as exc:
        raise SystemExit(f"provider payload escapes install root: {relative}") from exc
    files.add(os.fspath(path))
    parent = path.parent
    while True:
        dirs.add(os.fspath(parent))
        if parent == install_dir:
            break
        parent = parent.parent

data["owned_files"] = sorted(files)
data["owned_dirs"] = sorted(dirs)
temporary = manifest.with_name(manifest.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, manifest)
PY
fi

PROXY="$INSTALL_DIR/integrations/codex_app/plugin/scripts/run-mcp.sh"
if [[ "$DRY_RUN" != true && ! -x "$PROXY" ]]; then
  echo "$PROG: warning: child MCP proxy not installed at $PROXY" >&2
  echo "$PROG: top-level agent-start-gemini will work, but delegated provider children require the core child MCP proxy." >&2
fi

if [[ "$CONFIGURE_MCP" == true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    echo "$PROG: would run $INSTALL_DIR/bin/agentstack-gemini-setup"
  else
    AGENTSTACK_HOME="$INSTALL_DIR" "$INSTALL_DIR/bin/agentstack-gemini-setup"
  fi
fi

echo "$PROG: provider runtime and Gemini payload installed into $INSTALL_DIR"
echo "$PROG: restart the ORRERY dashboard service to load the new provider registry."
echo "$PROG: authenticate once with 'agy', then Gemini is available from the dashboard or agent-start-gemini."
