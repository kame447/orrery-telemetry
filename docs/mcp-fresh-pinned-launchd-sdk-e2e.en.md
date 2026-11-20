# Pinned fresh MCP in an actual macOS LaunchAgent (fork stage 26)

The previous pinned runtime test launched a detached Python subprocess, not launchd. This opt-in test creates a uniquely named, non-autostart private LaunchAgent using a disposable gateway release, Mail source, third-party packages, temporary HOME/DB, paired state and dedicated loopback Dashboard. It neither targets the production Dashboard on port 8770 nor changes any live service or agent.

The LaunchAgent executes the exact `python -I -S -B -c` command produced by `build_command`, performing source, dependency and import-origin attestation inside the serving Python interpreter. Before disclosing a pairing secret, the test matches the loaded plist/PID and the actual server owner of its TCP connection via launchctl and lsof, then verifies effective MCP grants over that same connection: six tools, the intended Mail instance, sender, recipient, messaging opt-in and no spawn. A separate real SDK connection exercises preview, Mail acceptance, receipt read, and idempotent re-commit, preserving `execution_status=unobserved`.

A `finally` block boots out only the private fixture label, verifies its PID/listener/registration have vanished, and checks that no secrets or message body entered its logs. Unknown lifecycle results are not reported as success, and unrelated services are never stopped.

```sh
MCP_FRESH_PINNED_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_pinned_launchd_e2e.py
```

This is a disposable fixture, not production installation, crash recovery, Secure MCP Tunnel acceptance, or proof that a running agent received, started, or completed work. Mail acceptance is not execution; unobserved is not evidence of non-delivery. Revert the test/docs/index/changelog to roll back. No API generation change.
