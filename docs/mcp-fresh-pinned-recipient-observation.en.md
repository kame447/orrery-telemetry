# Authenticated fixture recipient read/ack for a pinned fresh MCP LaunchAgent (fork stage 34)

This extends the opt-in real macOS LaunchAgent acceptance for a pinned immutable fresh MCP release. The test uses only a unique LaunchAgent label, isolated HOME, Mail database, Dashboard, pairing, policy, and dynamic loopback ports. It never contacts production MCP, Mail, Tunnel, or a live agent.

After a real MCP SDK session verifies six tools, process PID/socket ownership, effective authority, preview, accepted Mail receipt, persistent status and idempotent commit, a separately bound fixture recipient uses the supported FreshBoundClient `fetch_inbox` API. It verifies the message ID, sender ID, body, and initial unread/unacknowledged state. The recipient explicitly calls `mark_message_read` and `acknowledge_message`, verifying both timestamps. The sender's `orrery_message_status` remains `execution_status=unobserved`. Mail acceptance, authenticated fixture retrieval, and explicit read/ack are distinct observations.

On 2026-11-17 the opt-in macOS Python 3.13 test passed (1 passed, 8 dependency warnings) with `MCP_FRESH_PINNED_LAUNCHD_E2E=1`. The source test is `integrations/chatgpt_mcp/tests/test_fresh_pinned_launchd_e2e.py`. Teardown in `finally` checks fixture-only LaunchAgent removal, PID/port disappearance and absence of credentials/body in logs.

A fixture client retrieving and acknowledging a message is not evidence that a real agent saw a notification, started work or finished it. A dedicated Tunnel-to-ChatGPT fresh connection, full CI, independent review, upstream acceptance and operator-confirmed production identity/allowlist are separate outstanding gates. Roll back by reverting the test, both guides, indices and changelog fragment. No API generation change.
