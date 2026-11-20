# Isolated live fresh MCP subprocess, SDK and Mail acceptance (fork phase 19)

Unlike the earlier in-process Gateway fixture and inert launchctl smoke, `integrations/chatgpt_mcp/tests/test_fresh_server_process_e2e.py` executes `fresh_server serve` in a separate Python process. It uses a temporary HOME, fresh Mail database and instance, two fictitious agents, a private sender context and policy, private pairing state, loopback dynamic ports, and a local-only Dashboard fixture. No production Mail, agent, Tunnel, credential, or LaunchAgent is contacted.

A real MCP SDK HTTP client discovers all six tools, reads the effective instance and message capability, previews the full body, commits once, reads the persistent receipt, and verifies that the same idempotency key does not generate a duplicate. A separate test changes the fixture Dashboard instance after preview and checks that commit fails without storing a message. A Mail acceptance receipt is not evidence of agent receipt, processing, or completion.

The Mail fixture guarantees cleanup; the added MCP subprocess is terminated in `finally` and its log is checked for secret or body exposure. These tests do not attest a pinned release, execute fresh MCP under launchd, exercise a hosted Tunnel or ChatGPT plugin, or approve a production cutover.

Run:

```sh
PYTHONPATH=integrations/chatgpt_mcp/src:packages/agentstack_mail/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_server_process_e2e.py
```
