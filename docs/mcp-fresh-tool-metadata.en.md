# Attest the public metadata of fresh MCP tools (fork stage 40)

The fresh live-process probe already pinned six tool names, input schemas, annotations and advertised security schemes, but ignored additional `tools/list` fields and the types of presentation fields. A server could claim extra capabilities through `execution`, `outputSchema`, `_meta`, or other unexpected fields while retaining the same six tool names and passing the check.

The isolated probe now permits exactly six fields per published tool: `name`, `title`, `description`, `inputSchema`, `annotations`, and `securitySchemes`. Titles and descriptions must be non-empty strings, but their text is not pinned. Unknown fields trigger fail-closed verification instead of silently accepting an unreviewed change to the public contract. The normal MCP server's tool inventory and authorization are unchanged.

Nine invalid cases were accepted before the fix in a synthetic loopback server (five unexpected metadata fields and four invalid title/description values). The corrected probe rejects all of them while retaining successful verification of the official tool definitions. Input constraints, persistent idempotency, effective sending authority and legacy operations are unchanged.

This fork-only stage stacks on Draft PR #95. Reverting it restores the weaker metadata check. A dedicated ChatGPT fresh Tunnel, real-agent processing start/completion, upstream acceptance and explicit production identity/allowlist approval remain unverified and are not claimed. Production Gateway, Mail, Tunnel, active agents, fork master and upstream are not modified.
