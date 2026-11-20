# Reject out-of-range JSON numbers and overdeep responses during fresh MCP attestation (fork stage 36)

The live-process probe validates JSON-RPC responses for `initialize`, `tools/list`, and `orrery_status`. Rejecting nonstandard `NaN` and `Infinity` literals alone was insufficient: valid JSON numeric syntax such as `1e999` or `-1e999` overflows to infinity when parsed with Python's default float conversion, bypassing the previous check even in otherwise unused response properties.

`fresh_process_probe` now rejects nonfinite parsed floats at any depth, while normal finite numbers such as `1e2` continue to work. Excessively nested JSON which triggers the parser's recursion limit is converted into a stable `MCP_STATUS_UNVERIFIED` refusal rather than an uncaught exception. Existing PID, TCP-owner and effective-authority checks remain unchanged. Response bodies and pairing secrets are not included in errors.

Synthetic loopback regression tests exercise both exponent signs at every RPC stage. This change does not establish dedicated ChatGPT Tunnel connectivity, actual agent processing or completion, upstream acceptance, or production identity/allowlist approval; production services are untouched. Revert the PR's code, tests, guides, indexes and changelog together if needed, noting that the prior parser accepted out-of-range numbers.
