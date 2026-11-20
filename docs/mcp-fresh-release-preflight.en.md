# Preflight for a pinned fresh MCP release (fork stage 5)

This step does not install, register, start, update or stop any service. It adds a read-only preflight for a separately labeled fresh MCP process, pinned release and the operator's intended Mail instance, sender and recipients. The existing legacy MCP, Tunnel, Mail and agents remain unchanged.

`orrery_chatgpt_mcp.fresh_release_plan.Plan` requires a dedicated `org.agentstack.chatgpt-mcp-fresh`-prefixed service label and port, excluding reserved legacy, Dashboard and Mail ports. The package SHA-256 digest and source commit are pinned. The expected Mail instance, stable sender ID, recipient IDs, names and kind=to are fixed operator inputs, never MCP model arguments.

`inspect(plan, verify_release, read_release)` reads an owner-private 0700 state directory and owner-only 0600 pairing/policy files. It rejects symlinks, hardlinks, duplicate JSON keys, unsafe permissions, noncanonical paths and mismatched authority. It then checks the manifest's release digest/source commit and calls the supplied release verifier. The intended production caller must explicitly provide the existing `service.verify_release` and `service.read_release`, not a permissive test substitute. The source commit is a manifest claim, not a cryptographic signature; the existing release check hashes package bytes but does not establish dependency integrity.

The response intentionally reports `live_process_verified=false`, `fresh_client_verified=false` and `dependencies_verified=false`. A successful preflight proves neither a working pairing secret nor current client credentials, backend availability, launchd identity, process arguments or MCP tool behavior. These require separate process proof and isolated end-to-end tests before enabling the service.

This module never calls launchctl, installs a release, starts a process, sends Mail, replays requests or discloses a pairing secret. Release copying, the actual process lifecycle CLI, pinned dependency environment and live process argument comparison remain unfinished. Do not use this component alone to change production services.

Ten stdlib-only fixture cases passed in an isolated Linux workspace. Mac Desktop Commander was unreachable; actual macOS launchd/lsof and integration with the existing service verifier and full repository CI are not claimed as tested.
