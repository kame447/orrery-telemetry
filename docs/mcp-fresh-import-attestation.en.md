# Offline import attestation for fresh MCP (fork stage 8)

This component verifies import dependencies before a future dedicated launchd service is enabled. It follows PR #62's inert plist staging. It does not bootstrap launchd, access Mail, send instructions, change configuration or start production agents.

The trusted local host calls `fresh_import_attest.check(plan, dependency, verify_release=service.verify_release, read_release=service.read_release)`. The existing static plan verifies the service label, instance, sender/recipient allowlist, pairing/policy permissions, pinned gateway release digest and source commit. An independently specified owner-controlled, nonwritable Mail package is fingerprinted over all Python filenames and contents. A mutable checkout is not an immutable Mail deployment. No database or credentials are created.

The checker then starts the selected Python in `-I -S -B` mode. The child verifies the full gateway and Mail package fingerprints before importing either. This excludes automatic site/.pth initialization and explicitly selects the trusted environment's site-packages directory. It imports the real `orrery_chatgpt_mcp.fresh_server`, `agentstack_mail.fresh_client`, `fresh_message_receipts` and FastMCP client, checks their expected first-party file origins, and verifies that `FreshBoundClient.receipt` and `get_message_receipt` exist. The subprocess receives a minimal environment without inherited AGENTSTACK_*, credentials, session details or arbitrary PYTHONPATH. Errors are sanitized into a fixed code.

A successful result has gateway_import, mail_import, receipt_contract and import_origins_verified set to true. The fields third_party_pinned, live_process_verified and service_started remain false. The version/hash and supply-chain provenance of all third-party distributions are not established. A one-time import check does not attest the later service process. Do not use this result alone as approval to activate a LaunchAgent.

The host-only `ImportPlan` specifies an absolute owner-controlled Mail package, its expected digest and explicit site-packages location. None can be selected through MCP tool arguments. A pinned, read-only Mail artifact still needs to be prepared separately; the checker cannot silently promote a mutable development checkout into a production dependency. The later activation stage must explicitly account for remaining unpinned dependencies.

Isolated tests use the actual macOS Python environment, the repository's real pinned gateway release builder, a temporary read-only copy of Mail and actual third-party FastMCP imports. They cover modified code, missing receipt contract, writable and linked files, missing dependency paths, environment sanitization and fixed errors. They do not demonstrate launchd startup or instruction delivery.

## Isolated import against an operator-approved dependency bundle (follow-up fork stage)

The stage-nine [third-party bundle manifest](../integrations/chatgpt_mcp/DEPENDENCY_BUNDLE.en.md) can be supplied as `ImportPlan.dependency_manifest`. Leaving it unset retains the old behavior and reports `third_party_pinned=false`; an unapproved environment is never silently upgraded to pinned status.

With a manifest, the host verifies the complete site tree and passes the exact approved SHA-256 to a `-I -S -B` child interpreter. The child verifies immutable first-party sources, checks the same third-party manifest before and after imports, and confirms that FastMCP, MCP, SQLAlchemy, SQLModel and Pydantic actually originate inside the pinned site-packages. The host verifies it again after child completion. A writable development venv is not an approved bundle.

Even on success, `service_started=false` and `live_process_verified=false` remain. This does not authenticate wheel provenance or prove that a separately launched daemon uses the same code. Dedicated launch/stop and live-process attestation remain later stages.
