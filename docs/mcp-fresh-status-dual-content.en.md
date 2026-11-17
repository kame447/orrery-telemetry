# Agreement between fresh MCP status representations (fork stage 37)

An MCP `tools/call(orrery_status)` response provides both machine-readable `structuredContent` and a JSON text entry in `content` visible to clients. Previously the live-process proof checked only the former. It could report effective authority as verified even when the text reported a different runtime mode, recipients, or send/spawn permissions.

The dedicated `fresh_process_probe` now requires exactly one text JSON object and compares it with `structuredContent` including JSON value types. Missing or extra entries, invalid JSON, duplicate keys, nonfinite numbers, mismatched booleans/numbers, or conflicting authority fail closed. Neither the pairing secret nor raw message bodies are returned. This adds no agent control or message sending.

Isolated synthetic-loopback regressions reproduce six incorrectly accepted cases before the fix and reject them after it. This stage does not prove real-agent processing, hosted Tunnel/ChatGPT acceptance, production identity/allowlist approval, or upstream acceptance. Installed MCP, Mail, Tunnel, and running agents are untouched.

Rollback is a single revert of this stage's code, tests, bilingual documentation/indexes and changelog fragment; doing so restores the unsafe ambiguity. Based on fork Draft PR #92.
