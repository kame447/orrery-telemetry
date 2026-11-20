# Pinning an isolated Dashboard target for fresh MCP (fork stage 24)

Previously, the pinned fresh MCP `Plan` and LaunchAgent specifications always targeted `http://127.0.0.1:8770`. Even a separate MCP and Mail process could therefore contact the production Dashboard during a supposed isolated test.

`orrery_chatgpt_mcp.fresh_release_plan.Plan.dashboard_url` is now configurable. Its default remains `http://127.0.0.1:8770` for backwards compatibility; no installed service is changed. An isolated fixture must explicitly provide `http://127.0.0.1:<dedicated-dashboard-port>`. The accepted form is strictly HTTP with the numeric IPv4 loopback host, an unambiguous port from 1024 through 65535, and no credentials, path, query, trailing slash, or port collision with the MCP listener. External addresses and DNS host aliases are rejected. An explicitly authorized fixture start additionally refuses the default 8770 target, requiring a dedicated Dashboard.

Both the pinned `fresh_runtime_command` / `fresh_attested_stage` and earlier `fresh_service_stage` / `fresh_service_status` derive the expected Dashboard URL from the plan. The entire plist is bound to the approval digest; switching targets after approval is rejected before pairing. This is a configuration safety improvement, not a claim that a live Dashboard, Mail identity, PID, process import provenance, Tunnel, or actual agent processing has been verified.

Rollback consists only of reverting this PR. Production services, fork master, upstream, Mail, Tunnel, agents and LaunchAgents are untouched. The targeted release plan, runtime command, staging, and status tests cover the contract. Live pinned-release startup and end-to-end agent processing remain open.
