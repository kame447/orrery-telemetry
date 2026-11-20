# Fixed JSON response depth for fresh MCP (fork stage 42)

On 2026-11-17, Python 3.14 CI for fork Draft PR #93 failed `test_overdeep_json_is_reported_as_probe_refusal` in runs 43988112355 and 43988192667. The existing probe relied on Python's JSON parser raising an exception for excessive nesting, but the decoder's accepted nesting depth varies between Python versions.

Stage 42 iteratively checks objects and arrays in decoded JSON-RPC responses to `initialize`, `tools/list`, and `tools/call`, rejecting any structure beyond 64 nesting levels. It applies the same bound to the parsed textual `content` representation when comparing status with `structuredContent`. Valid response contracts remain unchanged; no credentials, service settings, effective grants, or send operations are modified.

Before the fix, an 80-level nested value inserted into each of the three RPC responses was incorrectly accepted (three failures reproduced on macOS Python 3.13). After the fix all were rejected. The existing 20,000-level regression must now fail safely irrespective of interpreter JSON recursion limits. GitHub Python 3.14 CI and independent review have not yet been completed.

Base: fork Draft PR #97. Reverting the change restores reliance on the interpreter's JSON depth limit. Dedicated ChatGPT fresh Tunnel acceptance, real agent processing and completion, production identity/allowlist approval, and upstream acceptance remain outstanding. Production services, running agents, fork master and upstream are not modified.
