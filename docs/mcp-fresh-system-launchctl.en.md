# Pin the system launchctl binary for fresh MCP process verification (fork stage 35)

Before disclosing the pairing secret to a running fresh MCP process, the verifier formerly resolved `launchctl` through `PATH`. An executable placed earlier in an untrusted process environment could forge the loaded plist and PID response, weakening the listener-ownership check and causing the secret to be sent to an unintended listener.

`fresh_process_probe.job_pid()` now invokes macOS `/bin/launchctl print` by absolute path. The existing `/usr/sbin/lsof` proof of the exact TCP connection, PID checks before and after the RPC sequence, and fail-closed behavior remain intact. The regression test asserts the exact executable path, while existing spoofed-plist, swapped-PID, timeout, and malformed-RPC tests exercise the pinned command.

This changes only the verification component. It does not modify production LaunchAgents, MCP, Tunnel, Mail, or active agents. Same-process import provenance, a dedicated ChatGPT Tunnel acceptance, real-agent receive/processing/completion, upstream acceptance, and operator-approved production identities and allowlists remain separate gates. Revert this PR's code, tests, guides, indices, and changelog fragment to roll back; the previous PATH-dependent executable lookup is not recommended.
