# Unambiguous JSON-RPC parsing for live fresh MCP attestation (fork stage 30)

A verified LaunchAgent PID and TCP owner do not make an ambiguous JSON-RPC response safe to interpret. The previous json.loads accepted duplicate object keys, retaining only the last value. A response containing both "runtime_mode":"legacy" and "runtime_mode":"fresh" could therefore be accepted based solely on the last member. Also, Python considers True == 1, so JSON-RPC "id":true could match request ID 1.

fresh_process_probe._request now rejects duplicate JSON object members at any nesting level and requires the response ID to be an actual int, not a bool. Failures become a fixed MCP_STATUS_UNVERIFIED error rather than surfacing response bodies or the pairing secret. Valid MCP replies retain the existing process-owner proof, pinned TCP socket and effective-grant checks. This code neither sends Mail nor restarts, resends or changes any service.

Three synthetic loopback MCP cases cover a boolean ID, duplicate equal-valued IDs and duplicate nested runtime-mode keys. All three were erroneously accepted before the change; after the change, all 26 focused probe tests passed. Full GitHub CI, independent review, dedicated Tunnel client acceptance and actual agent receipt/processing are not established by these tests. Revert this stage's code, tests, bilingual docs and changelog fragment to roll back. The default-disabled configuration, production MCP/Mail/Tunnel, active agents, fork master and upstream remain unchanged.

Focused test: python3 integrations/chatgpt_mcp/tests/test_fresh_process_probe.py -q. This does not change the Dashboard public API generation.
