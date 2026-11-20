# Explicit recovery after an isolated pinned fresh MCP process crash (fork stage 28)

Stage 26 verified orderly shutdown of a dedicated macOS LaunchAgent, not recovery after abrupt process death. This additional opt-in test creates a wholly disposable pinned release, third-party dependency bundle, Mail database and instance, synthetic agents, dynamic loopback port and non-autostart LaunchAgent. It never signals, restarts or reconfigures the installed MCP, Mail, Tunnel or real agents.

The test checks the exact launchd label/plist/PID and uses an authenticated TCP connection to attest the listener owner and effective Mail instance, sender, recipient grants and disabled spawn capability. Only after this match does it send SIGKILL to the verified fixture PID. With `KeepAlive=false`, it verifies no automatic restart and explicitly invokes `kickstart` for that same fixture label. It then attests the new PID and connection, reads the persisted receipt through the actual MCP SDK and confirms the same idempotency key cannot duplicate the accepted Mail message.

The opt-in macOS Python 3.13 run passed (`1 passed, 5 warnings`). Teardown verified the disappearance of the fixture registration, old and new PIDs and listener, and checked that the log contains no pairing secret, message body or registration token. This verifies a controlled crash-and-explicit-restart scenario only; it does not authorize replay on an unknown result, prove the agent started or finished work, or test a real Secure MCP Tunnel or ChatGPT session. Upstream acceptance, explicit production identity/allowlist approval, full CI and independent review remain outstanding.

Run only on an explicitly authorized, isolated Mac GUI session:

```sh
MCP_FRESH_PINNED_CRASH_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:integrations/chatgpt_mcp/tests:packages/agentstack_mail/src \
  .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_pinned_crash_recovery_e2e.py
```

The test is skipped by default. Revert the opt-in test, guides, indexes and changelog fragment to roll back. No dashboard API generation change.
