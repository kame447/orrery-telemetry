#!/usr/bin/env bash
# Install the experimental Google Antigravity / Gemini provider into an existing
# ORRERY installation. This is intentionally opt-in while the main installer
# wiring is under review.
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

BIN_DIR="$INSTALL_DIR/bin"
HOOKS_DIR="$INSTALL_DIR/hooks"
FILES=(
  "bin/agent-start-gemini"
  "bin/agentstack-gemini-bootstrap"
  "bin/agentstack-gemini-setup"
  "bin/agentstack-gemini-child-mail"
  "bin/agentstack-gemini-stream"
  "hooks/spawn_gemini_child.sh"
)

for relative in "${FILES[@]}"; do
  src="$REPO_ROOT/$relative"
  [[ -f "$src" ]] || { echo "$PROG: missing source file: $src" >&2; exit 1; }
  case "$relative" in
    bin/*) dst="$BIN_DIR/${relative#bin/}" ;;
    hooks/*) dst="$HOOKS_DIR/${relative#hooks/}" ;;
    *) echo "$PROG: unsupported payload path: $relative" >&2; exit 1 ;;
  esac
  echo "$PROG: copy $src -> $dst"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chmod +x "$dst"
  fi
done

PROXY="$INSTALL_DIR/integrations/codex_app/plugin/scripts/run-mcp.sh"
if [[ "$DRY_RUN" != true && ! -x "$PROXY" ]]; then
  echo "$PROG: warning: child MCP proxy not installed at $PROXY" >&2
  echo "$PROG: top-level agent-start-gemini will work, but delegated Gemini children require the core child MCP proxy." >&2
fi

if [[ "$CONFIGURE_MCP" == true ]]; then
  if [[ "$DRY_RUN" == true ]]; then
    echo "$PROG: would run $BIN_DIR/agentstack-gemini-setup"
  else
    AGENTSTACK_HOME="$INSTALL_DIR" "$BIN_DIR/agentstack-gemini-setup"
  fi
fi

echo "$PROG: Gemini provider payload installed into $INSTALL_DIR"
echo "$PROG: authenticate once with 'agy', then launch with '$BIN_DIR/agent-start-gemini'."
