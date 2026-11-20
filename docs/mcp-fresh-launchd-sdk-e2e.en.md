# Isolated real macOS LaunchAgent, fresh MCP SDK and Mail acceptance (fork stage 20)

This opt-in test combines stage 18's real launchctl lifecycle of an inert fixture with stage 19's real fresh MCP subprocess/SDK/Mail test. It is not a pinned-release deployment or a production service cutover.

Run `integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py` only on a macOS interactive login session with `MCP_FRESH_LAUNCHD_E2E=1`. The test uses the development venv and test-only PYTHONPATH, a temporary HOME, private Mail database/instance, fictitious sender and recipient, policy, state, fake Dashboard, free loopback port, and an unpredictable `org.agentstack.chatgpt-mcp-fresh.fixture-e2e-...` label. It never connects to production Mail.

The plist is mode 0600, with both RunAtLoad and KeepAlive disabled. After verifying the label is absent, the test bootstraps it, verifies the exact registered plist and inactive state, kickstarts once, then verifies a real PID and MCP listener. A real FastMCP client discovers the six tools and validates status, full preview, commit with Mail acceptance, a persisted receipt, and idempotent re-commit. A finally block bootouts only the dedicated label and checks job removal, old PID disappearance, and no secret or message body leaked to logs. Failure to verify cleanup is a failure, never a pass.

```sh
MCP_FRESH_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py
```

The test establishes acceptance by isolated Mail, not notification delivery, agent receipt, processing start, or completion. Fixed-release runtime import attestation, restart/recovery, hosted Tunnel/ChatGPT acceptance, upstream approval, and production identity/allowlist reapproval remain outstanding. `execution_status=unobserved` is not proof of execution. No live gateway settings are changed.

See [real launchd fixture lifecycle](mcp-fresh-launchd-smoke.en.md) and [real subprocess SDK/Mail E2E](mcp-fresh-process-sdk-mail-e2e.en.md).
