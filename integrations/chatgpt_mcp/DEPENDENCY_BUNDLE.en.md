# Third-party dependency bundle pinning for fresh MCP (fork stage nine)

This offline, read-only component checks an operator-approved immutable Python dependency tree before a dedicated fresh MCP service can be enabled. It does not install, launch or modify production services, Mail, Tunnel, launchd or agents.

The inventory hashes every relative path, node kind, permissions, size and complete file bytes under a dedicated site-packages tree. It also records distribution Name/Version metadata and requires at least FastMCP and the MCP SDK. Duplicate distributions, .pth or egg-link overlays, pyc/cache, symlinks, hardlinks, unusual nodes, untrusted ownership or writable permissions and count/size overruns are rejected.

inspect(site) returns a proposed digest and distribution inventory without writing files. An operator must separately verify trusted wheel sources, hashes, lockfile and installation records, and create an owner-private 0600 JSON manifest with exactly kind, site_packages, sha256 and distributions. Nothing is approved automatically. verify(manifest_path, expected_site) confirms that the complete immutable tree matches the authorized manifest and otherwise returns a fixed refusal code.

A successful at-rest pin is not a supply-chain provenance proof or a live process attestation. The response explicitly retains live_process_verified=false, origin_authenticity_verified=false and service_started=false. Do not weaken the policy to accept an editable development virtual environment. A separate read-only dependency artifact is needed.

PR #63 already verifies first-party gateway and Mail source imports. Integrating this checker with that isolated Python process, revalidating the effective code loaded at service start, and safely installing/starting/stopping the dedicated service are separate follow-up changes. Tests use synthetic temporary dependency trees only.
