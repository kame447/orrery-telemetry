# Isolated fresh MCP LaunchAgent start (fork stage 13, Draft)

This operator-only API extends stage 12 runtime attestation with a narrowly scoped, explicitly approved launchctl bootstrap and kickstart for a dedicated fixture LaunchAgent. It does not expose an MCP tool and cannot operate existing production MCP, Mail, Tunnel, or agents.

orrery_chatgpt_mcp.fresh_attested_start.preview verifies the pinned release, dependency bundle, private policy and exact staged plist bytes and returns a start-specific approval digest without launching anything. start requires that digest, an explicit fixture_mode=True, a dedicated org.agentstack.chatgpt-mcp-fresh.fixture... label, a trusted observation that the label is unloaded, and an available loopback port. It performs bootstrap only once.

After bootstrap, the trusted read-only lookup must report the same label and exact staged plist path before a non-killing kickstart is attempted. Success is returned only after stage 12 check_running verifies the actual PID, six MCP tools and effective grant. Callers must provide trustworthy is_loaded and loaded_plist_path implementations: an error or unknown result is never treated as absence. A free-port probe does not reserve the port, so runtime PID/connection attestation remains mandatory.

After any launchctl attempt, failures, timeouts and unexpected results are ambiguous. This code never retries, bootouts, kills, auto-rolls-back or touches another job. It reports FRESH_START_OUTCOME_UNKNOWN for manual inspection. Stop, restart and rollback remain unimplemented, and a successful process check does not establish in-process import provenance, actual Mail delivery, agent receipt or agent processing.

The fixture label alone does not establish production isolation. The operator must independently confirm a dedicated temporary HOME, state/DB, pinned release, fictional authorized instance and agent identities, free port and unique job label before any real fixture activation. Production fresh cutover remains forbidden pending upstream acceptance and explicit user approval of identity and allowlist.

The new tests mock launchctl and every connection/process probe; no real Mac job, Mail fixture, ChatGPT or Tunnel connection has yet been exercised. See [stage 12 runtime attestation](mcp-fresh-attested-status.en.md).
