# Isolated fresh MCP LaunchAgent restart and durable receipt (fork stage 21)

Stage 21 extends the real macOS LaunchAgent SDK/Mail acceptance test with one deliberate stop and restart of the same private fixture. It does not touch production MCP, Mail, Tunnel, or running agents.

The test requires an interactive macOS session and explicit MCP_FRESH_LAUNCHD_E2E=1. It creates a unique service label, private HOME, Mail DB/instance, synthetic sender/recipient, state, and loopback port with RunAtLoad and KeepAlive disabled. After SDK preview/commit confirms Mail acceptance, launchctl bootout must remove the registration and old PID. Only then is the same fixture plist bootstrapped and kickstarted once.

An external MCP SDK verifies a different live PID, the same Mail instance and persisted receipt, and no additional Mail row after the identical preview/digest/idempotency key is committed again. Teardown verifies that the fixture job and both PIDs are gone and that logs omit private body and secrets. An uncertain result never triggers an automatic resend or restart.

Run: MCP_FRESH_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py

This proves only an orderly isolated restart. Unexpected-crash recovery, pinned release import attestation under launchd, real Tunnel/ChatGPT, agent receipt/processing/completion, and production identity/allowlist acceptance remain unverified. execution_status=unobserved does not prove processing.

See [stage 20 real LaunchAgent acceptance](mcp-fresh-launchd-sdk-e2e.en.md).
