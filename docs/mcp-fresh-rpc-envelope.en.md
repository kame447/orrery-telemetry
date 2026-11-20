# Reject contradictory JSON-RPC responses during live fresh MCP attestation (fork stage 32)

The fresh LaunchAgent attestation probe previously accepted a JSON-RPC 2.0 response containing both `result` and `error` and used only the successful result. A JSON-RPC response must not include success and failure together. Treating contradictory messages as success can falsely attest initialization, advertised tools, or effective messaging authority.

The probe now rejects any top-level `error` member in a purported successful response with `MCP_PROTOCOL_UNVERIFIED`, regardless of its value. This is a read-only verifier change: it does not change the service, delivery policy, send contract, or production configuration, and does not expose secrets or message bodies.

Three synthetic loopback cases (initialize, tools/list, and tools/call for orrery_status) were incorrectly accepted before the fix and are rejected afterward. On macOS Python 3.13, 28 focused unittest cases passed. GitHub CI and independent review are separate gates. Dedicated ChatGPT Tunnel acceptance, real agent processing/completion, and production identity/allowlist approval are not established here.

Rollback by reverting this stage's code, tests, bilingual documentation, and changelog fragment. Default-disabled settings, production MCP/Mail/Tunnel/agents, and the Dashboard API generation are unchanged.

Focused command: `python3 -m unittest integrations.chatgpt_mcp.tests.test_fresh_process_probe -q`.
