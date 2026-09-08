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

Before copying any provider payload, the optional installer validates the core
install manifest and checks that the installed Dashboard core exposes the
symbols required by the provider extension. A clearly incompatible core is
rejected before mutation with an instruction to update/reinstall ORRERY core.
This is a fail-closed compatibility guard for the extension boundary, not a
claim that every semantically incompatible future core can be detected by a
static symbol check.

The MCP setup owns only `mcpServers.orrery-mail` in
`~/.gemini/config/mcp_config.json` and preserves other server definitions.
An existing empty or whitespace-only config is treated as an uninitialized
configuration; malformed non-empty JSON is rejected without overwrite.
Existing files are backed up before they are changed. The global entry stores
only the local session-bound wrapper command; it does not persist an ORRERY
bearer token, an agent owner token, or an owner-token file path. The core
uninstall removes this entry only while it still matches the command installed
by ORRERY, so a user-replaced entry is preserved.

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
the session-bound wrapper resolves the current agent's mode-0600 owner-token
file at MCP process start instead of embedding the token or token-file path in
Antigravity configuration.

Top-level lifecycle follows the dashboard's observation model rather than
forcing a synthetic state transition. The launcher deliberately leaves the
human-interactive shell alive after `agy` exits, including when it was invoked
from an already-existing tmux pane. The dashboard can therefore observe that
the provider process is gone while the tmux shell remains and classify the
session as `finished`. This differs intentionally from delegated children,
whose launcher-owned command chain ends after cleanup and lets their tmux
session disappear.

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
- the token stays in a mode-0600 runtime file and is never placed in the child
  task prompt or process argv by this integration;
- a dedicated git worktree and `exp/<agent-name>` branch are created;
- declared resources are reserved before Antigravity starts;
- project-relative declared resources are presented to the model as absolute
  paths rooted in that child's worktree, so the task does not need to rediscover
  the original checkout;
- `.agents/mcp_config.json` points `orrery-mail` at the same session-bound
  `agentstack-gemini-mcp` wrapper used by top-level sessions; the workspace
  configuration contains neither the owner token nor its runtime file path;
- the wrapper derives the current child identity from the inherited session
  environment, resolves the corresponding runtime token file internally, and
  then execs the existing narrow stdio proxy;
- the child-specific `.agents/mcp_config.json` is kept out of Git through a
  child-owned mode-0600 excludes file injected into descendant Git processes
  with `GIT_CONFIG_COUNT`; linked worktrees' shared `.git/info/exclude` is not
  mutated, so overlapping Gemini children cannot remove each other's rule;
- the task is sent to `agy` over streaming stdin rather than a process argument;
- Antigravity runs in headless stream mode inside tmux;
- the launcher sends the final textual result to the parent through ORRERY Mail
  and releases reservations even if the model itself never calls MCP;
- a nominal `SUCCESS` result with no textual response, or a run that reports
  denied required actions, is treated as incomplete rather than silently
  reported as a successful child result;
- after result reporting, the runner releases reservations, soft-retires the
  child identity, removes transient credential/config/excludes state, and exits
  as the sole tmux command; the tmux session then disappears naturally, so the
  dashboard observes `gone` / `retired` instead of a persistent `finished`
  shell husk;
- the worktree is retained after completion so the parent can review or merge
  the child's branch.

The provider-aware dashboard follows the actual child process below the tmux
shell wrapper. A wrapped child is considered running only when the measured
provider runtime and the ORRERY Mail program identity agree (for Gemini,
`agy` plus `program=antigravity`). While that headless runtime is alive, its
card has the normal `EXIT` control. `EXIT` sends SIGINT only to the freshly
re-measured `agy` process, leaving the launcher-owned bash runner alive so it
can perform result reporting, reservation release, retirement, and transient
state cleanup. Interactive top-level Gemini sessions continue to use the normal
interactive `/exit` path. Once a delegated child is no longer running and is
shown as finished/gone/retired, the running-only `EXIT` control is intentionally
absent.

The dashboard does not manufacture `finished` or `gone` as provider-specific
workflow states. It reports what remains measurable: a stopped `agy` with a
surviving shell is `finished`; a completed child whose cleanup chain has ended
and whose tmux session has disappeared is `gone` (or `retired` after the
launcher soft-retires its ORRERY Mail identity).

The child launcher does not auto-approve Antigravity permissions and does not
broaden the user's global permission rules. Tasks are instructed to stay inside
the isolated worktree and not search the source checkout, `$HOME`, or parent
directories for declared resources. In headless mode, operations that still
require approval but are not pre-authorized by the user's Antigravity permission
rules may be soft-denied. Configure only the specific commands/MCP tools that
you intend to allow.

The owner-token file is mode 0600 and its path is deliberately absent from the
workspace MCP configuration, but Antigravity still runs as the same operating-
system user as ORRERY. This integration therefore reduces accidental credential
exposure in configuration/argv; it is not an OS-level sandbox boundary against
a same-user process with permission to inspect that user's files.

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

Remote servers use the `serverUrl` field. ORRERY uses local stdio wrapper
entries for both top-level and delegated-child identity binding. The wrapper
resolves the owner-token runtime file only when the MCP process starts, so the
Antigravity MCP configuration does not need to store bearer headers, owner
tokens, or owner-token file paths.

The shared narrow proxy still uses the historical `codex:` external-ID
namespace internally for direct bindings. Provider identity is carried
separately in the `program` field (`antigravity` for Gemini), so the namespace
prefix does not mean the Gemini session is registered as Codex.

## Current status

The provider is implemented on `feat/gemini-provider`. Top-level macOS launch,
ORRERY Mail registration, dashboard display, and Antigravity MCP access have
been exercised successfully. The first delegated-child macOS run successfully
exercised preregistration, worktree creation, reservation and the child-bound
MCP proxy, but exposed both an out-of-worktree resource lookup and a dashboard
liveness gap for the bash-wrapped headless runtime. Resource paths are now
anchored to the child worktree, denied/empty nominal-success responses are
rejected, child Git ignore state no longer mutates shared linked-worktree
metadata, and the workspace MCP config no longer embeds the owner's token-file
path. The dashboard follows the provider process below its shell wrapper and
routes headless `EXIT` to the revalidated runtime only. The top-level launcher
preserves its interactive shell consistently whether it creates a tmux session
or is invoked from an existing one, while delegated child runners clean up and
terminate their own tmux sessions. These paths have regression coverage.

A repeated real Antigravity delegated-child completion run and a live Dashboard
`EXIT` run on macOS are still required before the repaired child lifecycle is
considered end-to-end verified.
