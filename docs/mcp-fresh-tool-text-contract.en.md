# Pin the client-visible text for fresh MCP tools (fork stage 41)

The fresh live-process probe already checked the six tool names, input schemas, authority annotations, and permitted metadata fields. However, any non-empty `title` and `description` would pass. A malicious or stale process could present client-visible instructions claiming that recipients can be changed or unknown sends replayed while all other contract checks still passed.

This stage independently pins the exact approved title and description for every one of the six tools. It does not trust the running server's definitions to supply the expected text. Even whitespace-only changes fail closed until the published contract is reviewed and updated. It does not change the tools exposed by the real server.

An isolated loopback server demonstrated 18 previously accepted mutations across six tools: a misleading title, a misleading description, and appended whitespace. The updated probe rejects those mutations and still accepts the official tool text. This is not a real LaunchAgent or Tunnel acceptance test. Recipient allowlists, no-replay unknown recovery, and the separation of Mail acceptance from execution remain unchanged.

This fork-only change stacks on Draft PR #96. Reverting it restores the previous non-empty-text check. Production MCP/Mail/Tunnel, active agents, fork master, and upstream are untouched. Dedicated ChatGPT fresh Tunnel testing, actual agent start/completion, production identity/allowlist approval, and upstream acceptance remain outstanding.
