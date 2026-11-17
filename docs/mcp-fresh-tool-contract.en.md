# Verify the complete fresh MCP tool contract (fork stage 38)

The previous live-process probe verified only the six tool names returned by `tools/list`. A server could advertise the same names but add arbitrary parameters to a send tool, weaken the preview body limit, or label a write tool as read-only without failing verification. An advertised `nextCursor` could also conceal later pages of undiscovered tools.

The probe now compares the pinned, fork-defined public contract of all six tools: `inputSchema` properties, required fields, types, bounds, `additionalProperties=false`, the read/write annotations, and advertised `securitySchemes`. It ignores unrelated presentation metadata such as descriptions and object order. The presence of `nextCursor` is rejected, including a null cursor, because the probe requires proof that the complete tool inventory was returned. This does not change the MCP server's pagination behavior; only the isolated authority probe fails closed.

An isolated synthetic MCP reproduced seven previously accepted invalid cases before the fix, covering hidden discovery pages, extra shell arguments, weakened preview bounds, misleading read-only annotations, changed security schemes, and unbounded read arguments. Official tool definitions remain accepted. The probe exposes no pairing secret, message body, or private credentials, and does not modify production settings.

This fork-only stage is stacked on Draft PR #93. Production Gateway, Mail, Tunnel, agents, fork master and upstream remain unchanged. A dedicated fresh ChatGPT Tunnel, real-agent processing start/completion, upstream acceptance and explicit production identity/allowlist approval remain outside this stage. Reverting restores the earlier weaker public-contract check.
