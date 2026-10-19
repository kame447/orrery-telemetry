#!/bin/bash
# This decision is synchronous and fail-closed; telemetry's runner is fail-open.
CORE_HOME="${AGENTSTACK_HOME:-$HOME/.agentstack}"
GUARD="$CORE_HOME/hooks/check-codex-file-reservation.py"
if [[ ! -f "$GUARD" ]]; then
  echo "CODEX RESERVATION GUARD unavailable: refresh the core installation and plugin, then review /hooks." >&2
  exit 2
fi
# A conditional also traps launch failures when the caller exported errexit.
if "${AGENTSTACK_PYTHON:-python3}" "$GUARD"; then
  exit 0
fi
exit 2
