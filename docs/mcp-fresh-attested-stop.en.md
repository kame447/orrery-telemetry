# Approved stop of an isolated fresh MCP fixture (fork stage 14, Draft)

This is an operator-only candidate for stopping the fixture introduced by the preceding approved-start stage. It is not a public MCP tool and is not connected to the existing production MCP, Mail, Tunnel, or agents. No real macOS launchd bootout has been performed for this stage.

`fresh_attested_stop.preview` read-only verifies a fixture-only label, pinned release, exact private plist, loaded job path, running PID, six MCP tools, and effective messaging authority. Its approval digest binds both the staged source and the observed PID and is domain-separated from start approval. It cannot recover or blindly unload an ambiguously started job.

`stop` requires explicit `fixture_mode=True`, the matching stop digest, repeated matching source/loaded-path/PID attestation, and an existing PID. Only then does it issue one `/bin/launchctl bootout gui/<uid>/<fixture-label>`. It rejects production labels and misleading near-prefixes such as `fixtureevil`. It returns `fresh_stop_verified` only when both the expected launchd job is unloaded and the previous PID has exited.

Any launchctl error, timeout, unknown post-state, or changed identity returns `FRESH_STOP_OUTCOME_UNKNOWN`. It never automatically retries, kills by PID, restarts, repairs another job, or assumes that unknown means stopped. PID checks use only signal 0. A time-of-check/time-of-use race cannot be fully eliminated; the operator must maintain an isolated fixture not controlled by another actor.

This stage does not establish running interpreter import provenance, Mail delivery, agent receipt or processing, or an end-to-end macOS shutdown. Its unit tests mock launchctl, PID and MCP probes. Restart and recovery of an ambiguous job remain outstanding. Production cutover still requires upstream acceptance and explicit owner verification of the new instance identity/allowlist.

See [approved fixture startup](mcp-fresh-attested-start.en.md) and [read-only process attestation](mcp-fresh-attested-status.en.md).
