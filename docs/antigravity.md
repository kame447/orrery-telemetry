# Google Antigravity / Gemini provider

ORRERY can launch Google Antigravity CLI (`agy`) as a third agent provider next
to Claude Code and Codex.

This integration uses the user's existing Antigravity authentication. It does
not set `GEMINI_API_KEY`, change the model provider, or enable
`--dangerously-skip-permissions`.

## Prerequisites

1. Install and authenticate Antigravity CLI.
2. Confirm `agy` is on `PATH`.
3. Confirm the normal ORRERY installation and ORRERY Mail are working.

Useful checks:

```sh
agy --version
agy models
```

## Install the provider payload

While the Gemini integration is experimental, install it into an existing
ORRERY checkout with the opt-in helper:

```sh
scripts/install-gemini-provider.sh
```

To also register the session-bound ORRERY Mail stdio proxy in Antigravity's
global MCP configuration:

```sh
scripts/install-gemini-provider.sh --configure-mcp
```

The MCP setup owns only `mcpServers.orrery-mail` in
`~/.gemini/config/mcp_config.json` and preserves other server definitions.
Existing files are backed up before they are changed. The global entry stores
only the local proxy command; it does not persist an ORRERY bearer token or an
agent owner token. The core uninstall removes this entry only while it still
matches the command installed by ORRERY, so a user-replaced entry is preserved.

## Launch a top-level Gemini session

```sh
~/.agentstack/bin/agent-start-gemini /path/to/project
```

Defaults:

- binary: `agy`
- model: `gemini-3.8-flash-high`
- reasoning effort: `high`

Override them with environment variables:

```sh
export AGENTSTACK_GEMINI_BIN=agy
export AGENTSTACK_GEMINI_MODEL=gemini-3.8-flash-high
export AGENTSTACK_GEMINI_EFFORT=high
```

The launcher registers the session with ORRERY Mail as program
`antigravity`, reconciles the tmux session name with the registered agent name,
and then starts the normal interactive Antigravity TUI. Antigravity permission
and sandbox settings remain user-owned. When the global MCP entry is enabled,
the session-bound proxy resolves the current agent's mode-0600 owner-token file
at process start instead of embedding that token in Antigravity configuration.

## Delegated Gemini child

Use the dedicated child launcher:

```sh
~/.agentstack/hooks/spawn_gemini_child.sh \
  --resources "src/**,tests/**" \
  "Implement the requested change and run the relevant tests." \
  /path/to/project
```

Delegated Gemini children deliberately differ from the interactive top-level
launcher:

- a child is pre-registered with its own ORRERY Mail owner token;
- the token stays in a mode-0600 file and is never placed in argv or a prompt;
- a dedicated git worktree and `exp/<agent-name>` branch are created;
- declared resources are reserved before Antigravity starts;
- `.agents/mcp_config.json` points `orrery-mail` at the existing local stdio
  proxy, which injects the child's identity without revealing its token;
- the task is sent to `agy` over streaming stdin rather than a process argument;
- Antigravity runs in headless stream mode inside tmux;
- the launcher sends the final textual result to the parent through ORRERY Mail
  and releases reservations even if the model itself never calls MCP;
- the worktree is retained after completion so the parent can review or merge
  the child's branch.

The child launcher does not auto-approve Antigravity permissions. In headless
mode, operations that require approval but are not pre-authorized by the user's
Antigravity permission rules may be soft-denied. Configure only the specific
commands/MCP tools that you intend to allow.

### Worktree cleanup

After reviewing/merging the child branch:

```sh
git -C /path/to/project worktree remove /tmp/cc-worktrees/<agent-name>
git -C /path/to/project branch -D exp/<agent-name>
```

## MCP details

Antigravity reads global MCP servers from:

```text
~/.gemini/config/mcp_config.json
```

and workspace-local servers from:

```text
.agents/mcp_config.json
```

Remote servers use the `serverUrl` field. ORRERY uses local stdio entries for
both the top-level session-bound proxy and delegated-child identity binding so
owner-token file paths can stay local to the proxy process rather than being
exposed to the model or stored as bearer headers in Antigravity configuration.

## Current status

The provider is implemented on `feat/gemini-provider` and is still undergoing
local macOS/Antigravity regression testing. Do not treat this document as a
claim that the full repository test suite has passed until that validation has
been recorded in the PR.
