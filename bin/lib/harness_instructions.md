# Applicability

Follow this ORRERY block only when ORRERY Mail tools are actually available.
Use only the existing resolved identity or session-bound runtime binding.
Provisional, retry or degraded bindings are unresolved. Never register a
different identity to get past a failure. Without an identity or binding,
skip Mail registration and notifications; editing guards remain in effect.

Your program and model come from the canonical launch task or runtime binding.
The model's provider and the program running it are separate facts. Do not
describe yourself as a different program because of a shared instruction file.
An uncoordinated session needs the operator's coordinated launcher; this
instruction file is not permission to create a registration.

# Global preparation context

When `AGENTSTACK_CLIENT_CONFIG` selects global mode, use the fixed client
context and stable IDs, not the legacy raw registration procedure below.
Child preparation uses `agentstack-preregister-child --prepare-only` with the
selected program and model; repeat the same command to recover its saved
registration intent. Prepare-only never means a running child. Registration
dispatch remains in `bin/lib/agentstack-register.sh`. Follow
`docs/global-child-client.md` and `docs/global-delivery-client.md` (or their
English versions) for the fixed handoff, common client, execution and delivery.
Unknown execution requires explicit operator resolution. Hook trust requires
prior human review; ordinary launch cannot approve it. Message writes use
typed JSON through `agentstack-runtime-client call-json send_message` with
`to_agent_ids`; use `replay-pending` to replay saved message bytes. With proxy
tools, follow the actual bound schema instead.

## Coordination (ORRERY Mail)

Use the first matching route only: embedded canonical task, bound proxy,
confirmed raw connection after shell registration, or recovery of the existing
raw identity. With no identity, skip registration and ask for the coordinated
launcher. If connection type is unknown, inspect the actual tool schema and
startup notice; do not infer proxy use from environment variables or fall back
from a proxy failure to raw registration.

<!-- include: ### 1. Embedded canonical task UNTIL ### 3. Raw/direct connection after shell registration -->

### Raw/direct connection and recovery

Only on a confirmed raw/direct connection, authenticate the separate MCP
session with the existing identity's token from
`${AGENTSTACK_RUNTIME_DIR:-$HOME/.agentstack/runtime}/agent_token_<name>`.
Pass it as `registration_token` on the first raw `fetch_inbox` or `whois`;
later calls in that authenticated MCP session may omit it. Never read a proxy
token. Shell registration does not authenticate the separate MCP session.

For recovery, resolve the existing name from `AGENT_NAME` or the exact startup
identity first. Never substitute an alias or an empty name. Run the documented
`agentstack-reregister` helper once with that existing name and its registered
program; do not guess the program from the model. The helper restores the
owner token without printing it; do not call `register_agent` afterward.
In global mode, exit 3 means observed without refreshing registration; pass
the existing program/model and rerun the helper to refresh it.

If recovery fails, report the exact failure and stop it. Never generate a new
token, register another identity, or retry blindly. A reserved child identity
stays reserved. Only exact `reason=credential-unavailable` calls for the local
operator's `docs/persistent-agents.md#credential-unavailable` procedure; never
run enrollment for the operator. An empty launcher-exported token variable is
not a token check: let the approved helper use its runtime files.

__ORRERY_IDENTITY_BINDING_UNRESOLVED__

<!-- include: ### Shared boundaries UNTIL ## Canonical coordination paths are fail-closed -->

<!-- include: ## Canonical coordination paths are fail-closed UNTIL ## File reservations — REQUIRED before editing -->

## File reservations — REQUIRED before editing

Editing guard coverage depends on the program and approved hooks. Shell writes
may be outside those guards. Reserve before every shared edit regardless of
guard availability; never approve your own production hooks. Doctor reports
missing, disabled or unverified protection.

Before editing a file under the project, use the selected connection route:

<!-- include: - Bound proxy: follow its actual schema and use `reserve_files`. Do not add UNTIL - On either route, **`ttl_seconds` must be at least 600**: composing the edit -->

On either route, `ttl_seconds` must be at least 600. Renew long edits and
release reservations when done; do not assume a tool releases them for you.
Forgotten reservations block other agents. If a path is already reserved by
another agent, coordinate over ORRERY Mail instead of editing it.

## MCP tool approval refusals

If a tool returns a refusal, stop the blocked work and report the exact
message. While an approval prompt is pending, wait for its result before
reporting a refusal. Do not change program configuration to bypass it or grant
permission on the operator's behalf.

<!-- include: Send your parent one line through ORRERY Mail `send_message`, following the UNTIL ## Skills -->

## Skills

Use installed skills through the program's supported skill mechanism, and
read their instructions before following them. Delegation uses the canonical
`__AGENTSTACK_HOME__/hooks/spawn_child.sh` launcher and the delegate skill;
do not invent a parallel flow. The log skill records structured work logs.

## Deterministic daemon development

The local operator-only daemon entry is documented in `docs/daemon-agents.md`. It does not change your existing coordination route or authorize you to create/enroll a live identity.
