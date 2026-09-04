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

FILES=(
  "bin/agent-start-gemini"
  "bin/agentstack-gemini-bootstrap"
  "bin/agentstack-gemini-setup"
  "bin/agentstack-gemini-child-mail"
  "bin/agentstack-gemini-stream"
  "hooks/spawn_gemini_child.sh"
  "hooks/spawn_gemini_preregistered.sh"
  "dashboard/server.py"
  "dashboard/server_core.py"
  "dashboard/provider_runtime.py"
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
