# Isolated pinned-release MCP SDK acceptance (fork stage 25)

Previous real macOS LaunchAgent E2E tests started the development server with PYTHONPATH. They did not exercise the complete pinned-release command which checks the fixed gateway, Mail source and third-party imports inside the same serving Python process.

`test_fresh_pinned_runtime_e2e.py` creates a disposable gateway release, a read-only Mail source copy, and an immutable copy of the development venv's third-party packages with an approved snapshot manifest. It starts the actual `python -I -S -B -c` command returned by `build_command`. That command verifies fixed imports twice before serving. The Mail database, synthetic agents, sender policy, pairing state and Dashboard all belong to the temporary fixture. The Dashboard uses a dedicated ephemeral 127.0.0.1 port, never the production default 8770.

A real MCP SDK client discovers six tools and verifies full preview, accepted Mail receipt, stable status, idempotent re-commit, and execution still marked `unobserved`. The child process is stopped in a `finally` block; its logs are checked for message contents and credentials. The test then tampers with a copied pinned gateway file and checks that the same command refuses with exit code 78 and creates no listener.

Because copying and hashing the dependency bundle is expensive, the test is skipped unless explicitly enabled:

```sh
MCP_FRESH_PINNED_RUNTIME_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_pinned_runtime_e2e.py
```

This is a detached, isolated Python process test, not a fixed-release LaunchAgent deployment, Tunnel/ChatGPT session, or proof of real-agent delivery, processing or completion. Mail acceptance is not execution. An `unobserved` state does not guarantee non-delivery. No live system, fork master or upstream changes are made. Roll back by reverting the test and its documentation, index and changelog fragment.
