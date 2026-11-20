# Bounded fresh MCP subprocess readiness in macOS CI (fork stage 27)

The private child-process MCP acceptance tests still failed with `private fresh MCP did not listen` on macOS Python 3.11/3.12/3.14, including runs after the CI venv isolation change in fork PR #79. The previous 20-second deadline does not distinguish a delayed import/initialization from a stalled child. The underlying cause is not yet proven.

Only the isolated `test_fresh_server_process_e2e.py` fixture changes in this stage: readiness has a bounded 60-second limit; an early child exit still fails immediately; failure to bind its dedicated loopback port still fails. The fixture retains process cleanup in `finally`. This does not alter production MCP configuration, Mail, Tunnel, or agents.

Failure diagnostics emit only four boolean signals: `child_running`, `startup_refused`, `listener_reported`, and `log_nonempty`. Raw process logs, pairing credentials, registration tokens, and message bodies are never placed in the assertion. A synthetic log containing a secret and a message body is used to check this redaction path.

The change is not a claim that the CI regression is fixed. Reassess with the new PR's macOS matrix, targeted acceptance, and independent review. Remain Draft while checks fail, are queued, or have not finished. If a child still fails after 60 seconds, use only the safe signals to distinguish a running but stuck process from an early exit; investigate separately rather than suppress the failure. This does not establish fixed-release production rollout, dedicated Tunnel acceptance, real-agent receipt/processing, or crash recovery.

Revert the test change and this stage's bilingual docs/index/changelog to roll back. No Dashboard API generation change.
