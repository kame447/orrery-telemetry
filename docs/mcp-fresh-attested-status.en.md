# Read-only live status for the attested fresh LaunchAgent (fork phase 12)

This phase checks the running process and effective authority of the inactive plist staged by phase 11. It does not load, start, stop, restart, or modify any MCP, Tunnel, Mail service, or agent.

`orrery_chatgpt_mcp.fresh_attested_status.check_running(plan, dependency, home, *, verify_release, read_release, read_secret, probe)` first recomputes phase 11's pinned gateway, Mail, and third-party dependency preflight. It compares the exact staged plist bytes to the expected bytes. The file must be owned by the current user, mode 0600, a single hardlink, and not a symlink.

The trusted `fresh_process_probe.probe` binds the loaded launchd plist and PID to the owner of the already established loopback TCP connection before sending the private pairing secret. MCP initialization, initialized notification, tools/list, and orrery_status must prove six expected tools, fresh mode, instance, sender, allowlisted recipients, message enabled and spawn disabled. The source preflight and plist are checked again after probing; changes cause a refusal.

Success returns only an observed PID and non-secret effective authority summary; errors return fixed codes. `runtime_import_provenance_verified=false` means the preparatory source check is not independent proof of import paths inside the *running* Python process. `delivery_observed`, `agent_receive_observed`, and `agent_processing_observed` are also false. This is a point-in-time observation, not a perpetual runtime guarantee.

The dedicated fresh LaunchAgent has not actually been started in this phase. A real MCP/Tunnel/agent acceptance test remains outstanding. `read_secret` must be a trusted private-state reader; do not replace the binding probe with a generic network request. Production cutover still requires upstream acceptance and explicit operator confirmation of identity/allowlist.

Run the isolated tests with `PYTHONPATH=integrations/chatgpt_mcp/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_attested_status.py`. The mocked probe does not verify a live service. See [phase 11 staging](mcp-fresh-attested-stage.en.md).