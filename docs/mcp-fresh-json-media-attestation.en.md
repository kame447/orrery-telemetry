# Refuse non-JSON media types and nonfinite numbers in fresh MCP process attestation (fork stage 33)

The pinned-release fresh MCP LaunchAgent probe used to accept a syntactically valid JSON body regardless of the response `Content-Type`. Python's default JSON decoder also accepts nonstandard `NaN`, `Infinity`, and `-Infinity`. Those responses are not valid JSON evidence of effective MCP authority.

For initialize, tools/list and orrery_status, the probe now requires the `application/json` response media type (including a normal `application/json; charset=utf-8` parameter), refuses invalid JSON constants at any depth, and continues refusing duplicate members. Failures expose no pairing secrets or arbitrary response bodies. This does not implement an SSE-streaming attestation transport or establish acceptance over a real ChatGPT Tunnel.

In synthetic loopback tests on macOS Python 3.13, six negative subcases incorrectly passed before the change; afterwards all 31 probe unittest cases passed, including nine NaN/Infinity/-Infinity negative subcases and a positive charset-header case. Full CI and independent review remain separate gates.

Production MCP/Mail/Tunnel/agents, default-off messaging, identity and allowlist approval, and the public Dashboard API generation remain unchanged. Revert only this stage's probe, tests, documentation and changelog fragment if needed.
