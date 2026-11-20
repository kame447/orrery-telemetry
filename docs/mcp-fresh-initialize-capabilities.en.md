# Pin fresh MCP initialization capabilities (fork stage 39)

The live-process probe previously accepted any dictionary as the server's `initialize.capabilities.tools` value. A process could therefore advertise `resources`, `prompts`, `logging`, or `experimental` capabilities despite returning the expected six `tools/list` entries. A `tools.listChanged=true` declaration would also permit the advertised inventory to change after discovery.

The isolated fresh-only probe now requires the actual fork-server initialization contract: `capabilities={"tools":{"listChanged":false}}`. Missing or extra keys, and numeric values masquerading as booleans, fail closed. Presentation-only fields in `serverInfo` remain version-flexible. Neither the production server nor the normal MCP transport is changed.

Seven invalid cases passed the old probe in a synthetic loopback MCP and are rejected after this fix. Valid initialization, complete tool discovery, and effective status checks remain accepted. The result contains no secrets. This proves only what the inspected process advertises, not ChatGPT's dedicated Tunnel connection or actual agent processing and completion.

This fork-only stage stacks on Draft PR #94. No running Gateway, Mail, Tunnel, agent, fork master, or upstream configuration is modified. Reverting restores the weaker capability verification. Production cutover still requires upstream acceptance and explicit operator identity/allowlist approval.
