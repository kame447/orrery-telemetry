# Strict boolean adapter flags for live fresh MCP authority (fork stage 31)

The live `fresh_process_probe.validate_effective_status` previously compared the three `adapter` flags using Python dictionary equality. Because Python considers `0 == False` and `1 == True`, a process returning numeric authority flags could pass as if it returned JSON booleans for `read_only=false`, `spawn_enabled_on_server=false`, and `message_enabled_on_server=true`.

This stage requires the exact `bool` type for each of these three flags before comparing their values and complete key set. Numeric flags fail closed as `EFFECTIVE_AUTHORITY_UNVERIFIED`; no pairing secret or response body is disclosed. This is a read-only verification change: it does not send or retry Mail, control agents, or start/stop LaunchAgents.

On macOS Python 3.13, three integer flag cases were incorrectly accepted before the fix. After the fix, all six integer/float substitution cases were rejected, and 27 synthetic loopback probe tests passed. Additional local validation is recorded in the PR; GitHub CI and independent review remain separate gates. Dedicated Tunnel acceptance, real agent receipt/processing, and production identity/allowlist approval are not established by this stage.

Rollback by reverting this stage's code, test, bilingual documentation, and changelog fragment. Default-disabled settings, production MCP/Mail/Tunnel, active agents, fork master and upstream are unchanged. The Dashboard public API generation does not change.

Focused command: `python3 -m unittest integrations.chatgpt_mcp.tests.test_fresh_process_probe -q`.
