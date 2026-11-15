# Read-only reconciliation for uncertain isolated fresh MCP results (fork stage 15, Draft)

This operator-only helper observes the dedicated fixture LaunchAgent introduced by stages 13 and 14 after an uncertain start or stop. It never retries bootstrap, kickstart, bootout, kill, restart or Mail delivery. It is not a public MCP tool or Dashboard endpoint.

`fresh_fixture_recovery.inspect(..., fixture_mode=True, previous_pid=...)` requires trusted, read-only launchd state and plist-path callbacks, existing pinned-source verification, and a real PID/MCP authority probe. A real isolated HOME, DB, Mail instance and fictional agent identity must be established independently before any live acceptance test.

For a loaded job it returns `running_attested` only after checking the exact staged plist, verified PID and effective grants, then re-attesting the same PID and permissions near the end of the observation. A PID replacement under the same label, a failed second probe, or a later plist/job-state change is rejected. This bounded double-check does not guarantee continuous operation after the observation. A different previous PID is reported as such, not mistaken for the earlier process. For an unloaded job it returns `job_unloaded_observed`, optionally requiring two negative existence checks on a previously observed PID. Unknown state, foreign plist, unverified PID, changed source and a still-existing old PID are rejected with fixed non-sensitive errors.

Every successful observation returns `restart_authorized=false`, `stop_authorized=false`, and `service_recovered=false`. An absent PID or unloaded job does not prove that a message was never sent, nor does it authorize restart. Mail acceptance, notification dispatch, agent receipt, processing start and completion are separate events. Unknown send results must use the authenticated read-only receipt path, never resend.

## Evidence and limits

The 15 new tests ran successfully in isolated Python in normal and `python -O` modes with launchd/PID/probe dependencies stubbed. These are not full repository/venv or live macOS tests. The Mac Desktop Commander connection failed and all four read retries terminated; therefore real launchd, MCP SDK/Tunnel and Mail/agent E2E remain untested. CI and independent review still require verification. No production service or upstream state is changed.
