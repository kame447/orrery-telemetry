# Live fresh MCP service authority attestation (fork stage 6)

This is a read-only component comparing saved launchd configuration with the responding MCP process. It does not install, start, stop, update, migrate legacy state or send Mail. It builds on PR #59 (pinned TCP peer proof) and PR #60 (fixed release and authority preflight).

`orrery_chatgpt_mcp.fresh_service_status.check_running(plan, plist_path, *, verify_release, read_release, read_secret, probe)` accepts an operator-fixed plan and plist path. The callbacks are trusted host dependencies, never values supplied by an external MCP caller. The `probe` must be the vetted `fresh_process_probe.probe` implementation to establish live process identity.

The function first performs the preflight check. It reads an absolute canonical plist using O_NOFOLLOW and rejects symlinks, hard links, unowned or group/world-writable files, oversized files and malformed plists. Exactly six plist keys are admitted: Label, ProgramArguments, EnvironmentVariables, RunAtLoad, KeepAlive and Umask. Undeclared socket activation or alternate launch commands are rejected.

The expected command is `[python, "-m", "orrery_chatgpt_mcp.fresh_server", "serve"]`. Exactly nine environment settings pin PYTHONPATH to the release, fresh mode, pairing authentication, message enabled, spawn disabled, a dedicated port/state/policy and the loopback Dashboard URL. RunAtLoad and KeepAlive must be true and Umask must be 0077. This is a contract for a later installer; the function never writes a plist.

Only after the static checks may the trusted pairing secret reader run. A URL-safe 43-character secret goes to the pinned process probe, which verifies the launchctl PID, lsof TCP peer and the same socket's MCP initialize, tool discovery and effective Mail sender/recipient authority. Errors use fixed labels without exposing secrets, request bodies or arbitrary peer responses.

Success reports on-disk release integrity, live process identity and effective authority, but explicitly leaves `fresh_client_delivery_verified=false` and `process_import_provenance_verified=false`. Matching current files does not prove which bytes a running Python process already imported or that an agent actually received and executed an instruction.

Sixteen synthetic stdlib tests passed under normal Python and `python -O` in isolated Linux. Real macOS launchd/lsof integration, actual release binding, GitHub Actions, hosted Tunnel delivery and service installation/rollback remain unverified. No production service or agent was changed.