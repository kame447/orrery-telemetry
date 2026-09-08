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
GEMINI_MCP_CONFIG="${AGENTSTACK_GEMINI_MCP_CONFIG:-$HOME/.gemini/config/mcp_config.json}"

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
The existing dashboard/server.py is core-owned and is never replaced by this
provider installer.
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

# The optional provider is an extension of a complete ORRERY core install. Do
# not copy a payload that can launch a top-level TUI but leaves delegated child
# identity, cleanup, or MCP binding broken. These are core-owned dependencies;
# the Gemini installer validates them but never replaces them.
CORE_REQUIRED_FILES=(
  "bin/lib/agentstack-register.sh"
  "hooks/project-context.sh"
)
CORE_REQUIRED_EXECUTABLES=(
  "bin/agentstack-preregister-child"
  "hooks/cleanup-child-agent.sh"
  "integrations/codex_app/plugin/scripts/run-mcp.sh"
)
for relative in "${CORE_REQUIRED_FILES[@]}"; do
  [[ -f "$INSTALL_DIR/$relative" ]] || {
    echo "$PROG: incomplete ORRERY core: required file missing: $INSTALL_DIR/$relative; update/reinstall ORRERY core before installing the Gemini provider" >&2
    exit 1
  }
done
for relative in "${CORE_REQUIRED_EXECUTABLES[@]}"; do
  [[ -x "$INSTALL_DIR/$relative" ]] || {
    echo "$PROG: incomplete ORRERY core: required executable missing: $INSTALL_DIR/$relative; update/reinstall ORRERY core before installing the Gemini provider" >&2
    exit 1
  }
done

FILES=(
  "bin/agent-start-gemini"
  "bin/agentstack-gemini-bootstrap"
  "bin/agentstack-gemini-setup"
  "bin/agentstack-gemini-mcp"
  "bin/agentstack-gemini-child-mail"
  "bin/agentstack-gemini-stream"
  "hooks/spawn_gemini_child.sh"
  "hooks/spawn_gemini_preregistered.sh"
  "dashboard/provider_server.py"
  "dashboard/provider_runtime.py"
  "dashboard/provider_classification.py"
  "dashboard/provider_launch_tracking.py"
  "dashboard/providers/registry.py"
  "dashboard/service_runner.py"
  "dashboard/assets/google.svg"
  "provider_specs/gemini.json"
)

# Validate every input before touching the installed tree. A malformed core
# manifest, incompatible dashboard core, provider manifest, or incomplete
# checkout must fail without a half-installed provider runtime.
for relative in "${FILES[@]}"; do
  [[ -f "$REPO_ROOT/$relative" ]] || {
    echo "$PROG: missing source file: $REPO_ROOT/$relative" >&2
    exit 1
  }
done
"$PYTHON_BIN" - "$MANIFEST" <<'PY'
import json
import pathlib
import sys

manifest = pathlib.Path(sys.argv[1]).expanduser()
try:
    data = json.loads(manifest.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid core install manifest {manifest}: {exc}")
if not isinstance(data, dict):
    raise SystemExit(f"invalid core install manifest {manifest}: expected object")
for key in ("owned_files", "owned_dirs"):
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"invalid core install manifest {manifest}: {key} must be a string list")
PY
"$PYTHON_BIN" - "$INSTALLED_SERVER" <<'PY'
import ast
import pathlib
import sys

server = pathlib.Path(sys.argv[1]).expanduser()
try:
    source = server.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(server))
except (OSError, SyntaxError) as exc:
    raise SystemExit(f"incompatible ORRERY core dashboard {server}: {exc}")

names = set()

def add_target(target):
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            add_target(item)

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            add_target(target)
    elif isinstance(node, ast.AnnAssign):
        add_target(node.target)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            names.add(alias.asname or alias.name.split(".", 1)[0])

required = {
    "HOOKS_DIR",
    "RUNTIME_DIR",
    "SEP",
    "SPAWN_SCRIPT",
    "_SPAWN_MODELS",
    "_agent_program",
    "_has_session",
    "_provider_of",
    "_render_dashboard_index",
    "_tmux",
    "_valid",
    "build_agents",
    "classify",
    "do_exit",
    "do_resume",
    "do_spawn",
    "graph_payload",
    "spawn_launch_status",
    "spawn_names_payload",
    "tmux_state",
}
missing = sorted(required - names)
if missing:
    joined = ", ".join(missing)
    raise SystemExit(
        "incompatible ORRERY core dashboard: provider extension requires "
        f"missing symbol(s): {joined}. Update/reinstall ORRERY core before "
        "installing the Gemini provider."
    )
PY
PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
from pathlib import Path
import sys

from dashboard.providers.registry import default_provider_registry

root = Path(sys.argv[1])
registry = default_provider_registry(install_root=root)
provider = registry.require("gemini")
if provider.program != "antigravity" or provider.dispatch != "adapter":
    raise SystemExit("invalid Gemini provider manifest: unexpected program/dispatch")
PY

for relative in "${FILES[@]}"; do
  src="$REPO_ROOT/$relative"
  dst="$INSTALL_DIR/$relative"
  echo "$PROG: copy $relative -> $dst"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    case "$relative" in
      bin/*|hooks/*|dashboard/provider_server.py|dashboard/service_runner.py)
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
  "$PYTHON_BIN" - "$MANIFEST" "$INSTALL_DIR" "${FILES[@]}" <<'PY'
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

if [[ "$CONFIGURE_MCP" == true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    echo "$PROG: would run $INSTALL_DIR/bin/agentstack-gemini-setup"
  else
    AGENTSTACK_HOME="$INSTALL_DIR" \
    AGENTSTACK_GEMINI_MCP_CONFIG="$GEMINI_MCP_CONFIG" \
      "$INSTALL_DIR/bin/agentstack-gemini-setup"
    "$PYTHON_BIN" - "$MANIFEST" "$GEMINI_MCP_CONFIG" \
      "$INSTALL_DIR/bin/agentstack-gemini-mcp" <<'PY'
import json
import os
import pathlib
import sys

manifest = pathlib.Path(sys.argv[1]).expanduser()
config_path = pathlib.Path(sys.argv[2]).expanduser().resolve(strict=False)
command = pathlib.Path(sys.argv[3]).expanduser().resolve(strict=False)
data = json.loads(manifest.read_text(encoding="utf-8"))
data["gemini_mcp_config"] = {
    "config_path": os.fspath(config_path),
    "server_key": "orrery-mail",
    "command": os.fspath(command),
}
temporary = manifest.with_name(manifest.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, manifest)
PY
  fi
fi

echo "$PROG: provider runtime and Gemini payload installed into $INSTALL_DIR"
echo "$PROG: restart the ORRERY dashboard service to load the provider entrypoint."
echo "$PROG: authenticate once with 'agy', then Gemini is available from the dashboard or agent-start-gemini."
