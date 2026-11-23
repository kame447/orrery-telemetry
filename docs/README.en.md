# Documentation index

[日本語](README.md) · [README](../README.en.md)

You can start with Claude Code, Codex, DeepSeek or Local LLMs. Choose a [standalone install](getting-started.en.md#standalone-installation-and-verification) or [install with the cockpit](https://github.com/gyroid-eth/orrery/blob/master/README.en.md#quick-start), then sign in to your chosen Claude/Codex CLI or follow [API setup for DeepSeek/Local](native-setup.en.md). Once configured, explore **Your first flight → help map → Full tour** from the cockpit’s `Settings → Getting started`. This index covers installation, updates, troubleshooting and detailed references.

Explore Telemetry’s own controls with `HELP MAP` in its header (beside `SETTINGS` in NETWORK). It annotates DECK / NETWORK controls, whether Telemetry is standalone or embedded in cockpit. In an agent detail panel, choose the panel’s own `HELP MAP` to annotate that panel’s controls.

Overview: the cockpit is the screen for operating terminals; Telemetry is the dashboard and agent-graph screen. The cockpit can embed the Telemetry screen.

![The cockpit is the screen for operating terminals; Telemetry is the dashboard and agent-graph screen. The cockpit can embed the Telemetry screen. Agents run in tmux, are visible from both screens, and talk through Mail](https://raw.githubusercontent.com/gyroid-eth/orrery/master/docs/images/cockpit_telemetry_relation.svg)

## Start here

- [Set up DeepSeek or Local LLMs](native-setup.en.md) — After the shared install, prepare dsh/Hermes, key storage and connection settings, then launch from the screen. This works for new and returning users; Claude/Codex login is not required.
- [Save an API key in Ubuntu WSL](wsl-keyring.en.md) — Prepare a keyring with the standard GUI and save the key through hidden input.

- [Choose a goal, provider, model and harness](getting-started.en.md#provider-model-harness) — Observe agents, create one with an API model, or coordinate agents. Check the [combinations and limitations](managed-catalog.en.md). Configured DeepSeek/Local models offer dsh/Hermes selection in NEW AGENT; runtime and API settings must be prepared separately.
- [ORRERY cockpit](https://github.com/gyroid-eth/orrery/blob/master/README.en.md#quick-start) — Install both with one line and start with the on-screen first-flight guide.
- [Standalone getting started](getting-started.en.md) — Audience, terms, standalone install, agent startup, and an end-to-end Mail check.

## Use the screen

- [Dashboard](dashboard.en.md) — Reference for DECK/NETWORK, history, RESUME/EXIT, REPLAY, and details.
- [Read from a designated phone](mobile.en.md) — The reader is off by default; owner admission, device restrictions, manual enable and disable steps.
- [MCP connection for ChatGPT](chatgpt-mcp.en.md) — Loopback-only gateway, Secure MCP Tunnel, pairing and approval on the Mac, and the setup the user performs.
- [Sending instructions from ChatGPT to an existing agent](chatgpt-mcp-message.en.md) — Dedicated Mail sender, recipient allowlist, preview and duplicate protection, delivery-state reconciliation (off by default).
- [Delegation and child agents](delegation.en.md) — ORRERY /delegate, differences from native children, and tool selection.
- [Launchers and identity](launchers.en.md) — agent-start, names, resume, /delegate and /log, and notification flow.
- [Computer use for children (Japanese)](computer-use.md)
- [Using Obsidian](obsidian.en.md) — Store logs, paper notes, and tasks in a vault and Daily Note.

## Candidate switch to empty Mail

See [current fork integration status](../integrations/chatgpt_mcp/README.en.md#current-fork-integration-status) for the current MCP implementation and verification scope. The stage guides below, including their unverified statements, record the state when each stage was written.

- [Fresh migration, shutdown and recovery](fresh-global-update.en.md) — Explicit steps for a new empty instance. Private startup/shutdown from saved settings is covered. Ordinary updates do not switch it; real OS managers/Finder GUI, real providers/trust/installation, final CI and an attended cutover remain unaccepted. Reactivation and App foregrounding are outside this cutover scope.
- [Read-only fresh send receipts](fresh-message-receipts.en.md) — Fork contract for authenticated sender-owned receipt lookup without replay; not_observed never authorizes resending.
- [Complete fresh MCP tool contract attestation](mcp-fresh-tool-contract.en.md) — Stage 38 rejects hidden discovery pages and mismatched input schemas or security annotations.
- [Fresh MCP initialization capability attestation](mcp-fresh-initialize-capabilities.en.md) — Stage 39 rejects extra advertised surfaces and changing tool inventory.
- [Bound live fresh MCP JSON response depth](mcp-fresh-json-depth-attestation.en.md) — Stage 42 rejects excessively nested JSON independently of Python's parser limits.
- [Pin client-visible fresh MCP tool text](mcp-fresh-tool-text-contract.en.md) — Stage 41 rejects unreviewed changes to all six tool titles/descriptions.
- [Fresh MCP tool metadata attestation](mcp-fresh-tool-metadata.en.md) — Stage 40 rejects extra public tool fields and malformed presentation metadata.
- [Agreement between fresh MCP status representations](mcp-fresh-status-dual-content.en.md) — Stage 37 refuses conflicting `content` and `structuredContent` authority.
- [Reject out-of-range fresh MCP JSON numbers](mcp-fresh-json-number-bounds.en.md) — Stage 36 refuses exponent overflow and excessive nesting in live-process attestation.
- [Pin system launchctl in fresh MCP process verification](mcp-fresh-system-launchctl.en.md) — Stage 35 avoids a PATH-based executable substitution before pairing-secret disclosure.
- [Pinned fresh MCP fixture recipient read/ack](mcp-fresh-pinned-recipient-observation.en.md) — Stage 34 observes authenticated test-recipient fetch/read/ack without claiming real-agent execution.
- [Strict JSON media type and finite values for fresh MCP](mcp-fresh-json-media-attestation.en.md) — Stage 33 refuses non-JSON response media types and nonstandard JSON numeric constants.
- [Reject contradictory live fresh MCP JSON-RPC responses](mcp-fresh-rpc-envelope.en.md) — Stage 32 refuses simultaneous success result and error in live authority attestation.
- [Strict fresh MCP authority flag types](mcp-fresh-strict-adapter-flags.en.md) — Stage 31 rejects numeric `0` / `1` instead of boolean adapter permissions.
- [Unambiguous fresh MCP JSON-RPC attestation](mcp-fresh-unambiguous-rpc.en.md) — Stage 30 rejects duplicate JSON members and boolean RPC IDs during live authority verification.
- [Post-RPC fresh MCP PID recheck](mcp-fresh-post-rpc-pid.en.md) — Stage 29 refuses changed or unverifiable LaunchAgent ownership after status RPC; does not guarantee future uptime.
- [Live fresh MCP process verification](mcp-fresh-process-probe.en.md) — Fork verification component for the loaded LaunchAgent PID, listener and effective delegated grant; service integration remains pending.
- [Pinned fresh MCP release preflight](mcp-fresh-release-preflight.en.md) — A fork-only read-only gate for a dedicated service, private policy and pinned release; no live service acceptance is implied.
- [Fresh MCP live service attestation](mcp-fresh-live-service-status.en.md) — Fork stage 6 checks pinned plist, release and responding PID/authority; installation and macOS acceptance remain pending.
- [Stage a dedicated fresh MCP LaunchAgent plist](mcp-fresh-service-staging.en.md) — Create one approved inactive service configuration without activating or replacing any running service.
- [Offline fresh MCP import attestation](mcp-fresh-import-attestation.en.md) — Fork stage 8 checks pinned first-party imports with real Python; third-party dependencies remain unpinned and no service is started.
- [Stage an attested fresh MCP LaunchAgent without starting it](mcp-fresh-attested-stage.en.md) — Bind pinned in-process verification to an approved, non-overwriting inactive plist; no launchctl activation.
- [Explicit crash recovery for isolated pinned fresh MCP](mcp-fresh-pinned-crash-recovery.en.md) — Stage 28 verifies Mac fixture SIGKILL, explicit restart, re-attested PID/grants, persistent receipts and no duplicate Mail send; not production recovery or agent execution.
- [Bounded fresh MCP CI subprocess readiness](mcp-fresh-ci-process-readiness.en.md) — Stage 27 bounds private-process startup to 60 seconds and emits only redacted boolean diagnostics for two outstanding macOS CI failures; resolution unverified.
- [Pinned fresh MCP in a real macOS LaunchAgent](mcp-fresh-pinned-launchd-sdk-e2e.en.md) — Stage 26 verifies pinned serving imports, launchd PID/socket ownership, SDK/Mail acceptance and private teardown; no live agent or Tunnel.
- [Pinned-release fresh MCP SDK E2E](mcp-fresh-fixed-release-sdk-e2e.en.md) — Stage 25 exercises fixed gateway/dependency verification in the serving process, real SDK and private Mail plus tamper refusal; no production launchd/Tunnel or agent processing.
- [Isolated Dashboard target for fresh MCP](mcp-fresh-dashboard-loopback.en.md) — Stage 24 pins a dedicated loopback Dashboard URL across the runtime, plist and approval digest; live acceptance remains pending.
- [Isolated Python for fresh MCP CI](mcp-fresh-ci-venv.en.md) — Use a per-job venv and verify import origins for parent and isolated child processes; not release pinning or production acceptance.
- [Read-only status of the attested fresh MCP process](mcp-fresh-attested-status.en.md) — Verify staged bytes, loaded PID, and effective MCP authority; it does not start or deliver messages.
- [Approved start of an isolated fresh MCP fixture](mcp-fresh-attested-start.en.md) — Stage 13 offers guarded bootstrap/kickstart with PID/grant attestation; live macOS and recovery acceptance remain pending.
- [Approved stop of an isolated fresh MCP fixture](mcp-fresh-attested-stop.en.md) — Stage 14 requires a live pinned PID and stop-only approval before a one-shot bootout; real macOS stopping and recovery remain unverified.
- [Read-only recovery observation after uncertain fixture operations](mcp-fresh-fixture-recovery.en.md) — Stage 15 reconciles a pinned job and previous PID, never authorizing retries; real Mac recovery remains unverified.
- [Real macOS launchd smoke for an isolated fixture](mcp-fresh-launchd-smoke.en.md) — Stage 18 opt-in OS lifecycle test, not real fresh MCP acceptance.

## Install and update

- [Update](install.en.md#upgrade) — Update while keeping existing settings.
- [Installation](install.en.md) — Requirements, standalone install, verification, upgrade/uninstall, and migration.
- [Configuration](configuration.en.md) — Look up AGENTSTACK_*, storage/exposure, model, and child settings.
- [Reusable Codex plugin lifecycle (Japanese)](codex-plugin-lifecycle.md) — Shared operations, scoped hook trust, and isolated verification.
- [Codex App integration](codex-app.en.md) — Add Desktop tasks/subagents as an optional provider.
- [Antigravity / Gemini](antigravity.en.md) — Add an optional provider and understand authentication and feature boundaries.
- [DeepSeek display and balance (Japanese)](dsh-stage-a.md) — Historical stage-A record. For current dsh / Hermes installation, see [Installation](install.en.md); child spawning needs provider and harness selection ([API](api.en.md)).
- [Persistent agents](persistent-agents.en.md) — Enrollment, interactive/headless startup, and credential recovery.
- [Deterministic daemons](daemon-agents.en.md) — Dedicated identity, canonical binding, notification waits, and shutdown.
- [Mail service updates](agentstack-mail-update.en.md) — Operational procedure to verify and replace a running Mail build.

## Windows and WSL

- [WSL2](install.en.md#installing-on-windows-wsl2) — Install inside Ubuntu for the standard Windows route; open it in a Windows browser.
- [Native Windows helper (experimental)](windows-local.en.md) — Community Mail/dashboard startup, separate from the standard WSL2 install.
- [Native Windows Codex launcher (experimental)](windows-codex-launcher.en.md) — Launch a preregistered child using a dedicated Windows tmux server.
- [Native Windows spawn (experimental)](windows-spawn.en.md) — Read native NEW AGENT name-catalog retrieval and the boundary that keeps SPAWN disabled.

## Troubleshoot

- [Troubleshooting](troubleshooting.en.md) — Isolate service, notification, spawn, authentication, and WSL problems.
- [Local HTTP trust boundary (Japanese)](local-http-security.md) — Which request origins Dashboard and Mail accept, and what they trust.

## Design and development

- [Hooks and helpers](hooks.en.md) — Events, block/release/cleanup, and operational guard specifications.
- [API reference](api.en.md) — Routes, requests/responses, and the compatibility-generation contract.
- [Design language](design.en.md) — The reference for dashboard appearance and motion.
- [Embedded tour events](embedded-tour-events.md) — Contract for embedded Telemetry to report real actions to a cockpit checklist.
- [ORRERY Mail internals](agentstack-mail.en.md) — Bundled-package boundaries, provenance, cutover, and authority.
- [Namespace contract and migration planning](project-removal-plan.en.md) — PR1 candidate APIs and gates; defaults stay unchanged and cutover belongs to later PRs.
- [Absolute reservation candidate](absolute-reservations.en.md) — PR2 shared normalizer, authenticated isolated store, client adapters and per-lease activity/GC; legacy mode remains the default.
- [Candidate Mail and delivery state migration](namespace-state-migration.en.md) — PR3 isolated snapshots, persistent stores and phase recovery; activation stays false.
- [Internal storage-origin B1 kernel](storage-origin-b1.en.md) — S′ candidate resolution, owner operations and pure peek; unpublished, with the default startup path unchanged.
- [Global server S1 preparation](global-server.en.md) — Isolated HTTP registration/reconnection, inbox, enrollment and authority epoch; legacy mode stays default.
- [Isolated global reservations/welcome](global-reservations.en.md) — S2c SQL authority, TTL, operator recovery and test migration; legacy remains default.
- [Isolated global messages and signals (S2b)](global-messages.en.md) — Eight tools, DB attachments, pure inbox, and signal reconciliation; legacy remains default.
- [Global runtime client PR4a preparation](global-runtime-client.en.md) — S1 registration, recovery, await and dedicated-HOME profile reconnection.
- [Mail update design](agentstack-mail-update-design.en.md) — Rationale and open decisions; use the update procedure for operations.
- [Mail performance gate design](agentstack-mail-performance-gate.en.md) — Design-only benchmark and adoption criteria, not a deployed benchmark procedure.
- [Claim/enrollment design](agentstack-mail-claim-enrollment-design.en.md) — Enrollment design supporting the product-decision ledger.
- [Third-party components (Japanese)](third-party.md) — Licenses and provenance for Mail, provider logos, and portraits.
- [Development entry](../CONTRIBUTING.md) — Build, validation, and contribution procedures.

Documents without a translation are labelled with their original language. Images/GIFs accompany their articles. From the repository root, run `python3 scripts/check_docs_index.py` to check index coverage.

- [Global child client preparation](global-child-client.en.md) — Fixed layout, registration intent replay, shared end and reservation hooks.
- [Caller, interruption and acceptance tables](global-child-client-contract.md) — Contract tables generated from the global client fixture.

- [Global client delivery and launch](global-delivery-client.en.md) — Fixed layout, notification retry, explicit recovery and provider preparation.
- [Shared tmux consumer boundary](runtime-tmux.en.md) — Pinned transports, target admission and repair diagnostics.

- [Instruction block migration](instruction-migration.en.md) — User scope, recovery and backup retention.

- [Dedicated tmux launch and termination acceptance plan (Japanese)](r2-tb-lifecycle-plan-v3.md) — Approved R2-Tb callers, schemas, interruption points and fixtures.

- [dsh thin launch (Japanese)](dsh-thin.md)

- [Native context and helper models](native-capabilities.md) — dsh / Hermes context observations and per-model capabilities.
- [Native catalog and effort capabilities](managed-catalog.en.md)
- [Isolated fresh MCP subprocess, SDK and Mail acceptance](mcp-fresh-process-sdk-mail-e2e.en.md) — Real HTTP acceptance with fictitious agents.
- [Real macOS LaunchAgent with isolated fresh MCP SDK/Mail](mcp-fresh-launchd-sdk-e2e.en.md) — Stage 20 opt-in PID, SDK, Mail acceptance, and cleanup test.
- [Durable receipt after isolated real LaunchAgent restart](mcp-fresh-launchd-restart-receipt.en.md) — Stage 21 orderly stop/restart, SDK receipt persistence and no replay.
- [Isolated fresh MCP recipient-owned inbox and ACK](mcp-fresh-recipient-owner-receipt.en.md) — Stage 22 protected synthetic recipient fetch/read/ack and restart durability; not actual agent processing.
