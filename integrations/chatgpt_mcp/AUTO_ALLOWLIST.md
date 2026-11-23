# ChatGPT MCP automatic recipient admission — isolated stage

This work is strictly for the fork, tracked in issue #108. It does not
authorize any new recipient at runtime and does not alter the running legacy
gateway, Mail daemon, operator profile, or LaunchAgent.

## Security boundary

The new private module, orrery_chatgpt_mcp.auto_allowlist, provides two
internal steps:

1. verify_mcp_spawn takes a committed, private MCP idempotency-ledger record,
   the ready dashboard launch result, the authenticated dedicated sender's
   identity, and a canonical Mail-helper lookup of the proposed recipient.
   Every source must agree on the new agent's name, stable numeric Mail ID,
   canonical Mail instance UUID, the exact legacy project key and numeric project
   ID, and the same registration timestamp. A different Mail project with a
   positive numeric ID is not sufficient. It also rejects a retired peer,
   a missing opt-in,
   an unknown outcome, a launch that is not ready, changed identity, a
   malformed numeric ID (including boolean), or a stale recipient.
2. append_verified_peer checks a prior SHA-256 profile snapshot and uses the
   dedicated daemon's existing lock to append one pinned name/ID pair to its
   owner-private v2 profile. It never evicts an existing peer; at 16 it fails.
   Writes are to a same-directory owner-only temporary file, fsynced,
   atomically replaced, then the directory is fsynced. A profile change or
   concurrent sender session fails closed rather than retrying.

The gateway must not expose either helper as an MCP tool. In particular, it
must never allow an MCP user to submit a candidate proof, manually set
mcp_authorized, or supply a target Mail ID.

## Read-only candidate observation

The internal `observe_unlisted_peer` helper now stages a private temporary
binding alongside the existing daemon profile, with one candidate added to its
fixed allowlist only for the duration of an authenticated, read-only
`runtime_status` and `whois` call. The live profile is not changed by this
step. The normal daemon profile lock prevents overlap with ordinary sending,
and the temporary file is removed even on lookup failure. It refuses a stale
profile snapshot, a full 16-peer list, conflicting name or Mail ID, unsafe
file permissions, and changed sender identity.

A read-only `whois` through the canonical daemon helper checks the candidate
against the sender's actual Mail project. Its numeric project ID is then
included in the verified sender observation for `verify_mcp_spawn`.
The later write must use the same SHA-256 snapshot. The helper does not
substitute for authorization or for the required independently attested
dashboard spawn record, and does not close Mail-side retirement races between
the final check and a later send.

## Not wired into production yet

Additional trusted plumbing and tests are still required before enabling:

- The dashboard's authoritative spawn and ready-status records must include
  the actual registered numeric Mail ID and Mail instance, and the exact
  legacy Mail project binding. Existing status records currently do not carry
  enough information; do not guess the ID from an agent name.
- After an explicitly approved MCP spawn_commit, record provenance in the
  private durable idempotency ledger, with exactly the original preview and
  result. No external trigger or unrelated dashboard launch qualifies.
- Verify the proposed peer by the canonical, pinned, dedicated daemon helper.
  The ordinary helper refuses looking up a recipient outside its current
  allowlist; it must not be bypassed. A carefully staged, owner-private
  candidate-pin profile may be used for a read-only verification before the
  live profile is updated. Reject other projects/instances.
- Add a separate, explicit, default-OFF operator service setting and a
  non-sensitive admission status (registered, not_registered, or
  precise failure) without conflating spawn acceptance with Mail admission.
  Preserve existing approval modes, send rate limits and session permissions.
- Test a temporary real Mail instance end-to-end with a synthetic spawned
  agent, message preview/commit/read/ACK/response, plus concurrent spawns,
  recipient retirement, identity reuse, unknown responses and a service
  restart. Do not send test instructions to live project agents.
- Carry out the 7-part review and the affected regression suite before a
  production service update. Never auto-enable this on installing code.

The pure-policy and owner-private file-operation unit tests can be run with:

    python -m pytest -q integrations/chatgpt_mcp/tests/test_chatgpt_gateway_auto_allowlist.py

The fresh/projectless adapter remains separately gated by fork #47 and
upstream #213 acceptance. No legacy identity, allowlist or credential may be
silently migrated into fresh Mail.
