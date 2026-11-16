# Isolated real macOS launchd smoke (fork stage 18)

Prior fixture start, stop and reconciliation tests primarily mocked launchctl. This stage adds an opt-in macOS test that executes real `launchctl bootstrap`, `print`, `kickstart`, and `bootout` against a unique disposable LaunchAgent label.

`integrations/chatgpt_mcp/tests/test_fresh_launchctl_macos.py` runs only on macOS with `MCP_LAUNCHD_E2E=1`. Ordinary CI and Linux/Windows skip it. It writes a private mode-0600 plist in a temporary directory, with an inert Python sleep process (90 seconds), `RunAtLoad=false` and `KeepAlive=false`. It checks that the job was absent, then inspects the actual registered plist path, LaunchAgent type, stopped state, running PID, and finally the unloaded job and departed PID. A `finally` block issues a single bootout for that unique test label. No production service, Tunnel, Mail, agent, or network port is changed.

Run with an existing development virtual environment, never with a production environment:

```sh
MCP_LAUNCHD_E2E=1 PYTHONPATH=integrations/chatgpt_mcp/src:packages/agentstack_mail/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_launchctl_macos.py
```

Passing the smoke validates only the OS-level fixture lifecycle. It does NOT prove a real fresh MCP runtime is started, imported from the pinned release, bound to its authorized identity, reachable by the official SDK/Tunnel, or observed by an agent. Those remain separate acceptance gates; no approval for production cutover is implied. A failed or ambiguous launchctl result must not be treated as safe for automatic retries.
