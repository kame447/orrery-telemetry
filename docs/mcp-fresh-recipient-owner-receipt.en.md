# Isolated fresh MCP recipient-owned inbox, read and acknowledgement (fork stage 22)

Stage 22 extends the stage 21 private macOS LaunchAgent/MCP SDK/fresh Mail E2E with a distinct synthetic recipient using its own protected `FreshBoundClient` binding. This is separate from MCP's Mail acceptance receipt. It is not evidence of a running agent, delivered notification, task processing or completion.

The regular fixture uses a private HOME, Mail DB/instance and synthetic sender/recipient. Before MCP preview/commit, the recipient inbox is empty. After commit, the authorized recipient fetches the exact message ID, sender ID, subject and full body; initial `read_ts` and `ack_ts` are null. Attempts by the authenticated sender to mark a message belonging to the recipient as read must fail with `NOT_A_RECIPIENT`. Only the recipient can mark it read and acknowledge it, then re-fetch the persisted timestamps.

The opt-in macOS test additionally uses the protected recipient client after the real LaunchAgent's MCP accepts Mail, then deliberately stops the private job, verifies the old PID is absent and restarts it once. A new recipient client confirms that the read/ack timestamps persist. The MCP `orrery_message_status` retains `execution_status=unobserved`; recipient fetch/read/ack is never translated into processing started or completed. The test never retries Mail or operates a production service.

Regular test: `PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_message_e2e.py`

Real Mac opt-in: `MCP_FRESH_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py`

Still unverified: notification delivery and actual agent processing/completion, crash recovery, pinned release imports under launchd, dedicated Tunnel/ChatGPT, upstream acceptance and production identity/allowlist approval. Production MCP, Mail, Tunnel and running agents are untouched.

See [durable receipt after restart](mcp-fresh-launchd-restart-receipt.en.md) and [real SDK/Mail acceptance](mcp-fresh-launchd-sdk-e2e.en.md).
