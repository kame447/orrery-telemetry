# ChatGPT MCP automatic recipient admission — fork candidate

This work is strictly for the fork, tracked in issue #108. The feature is
OFF by default. The candidate has been exercised only in isolated fixtures;
the running legacy gateway, Mail daemon, operator profile and LaunchAgent
are not changed by these tests.

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
   concurrent sender session fails closed for that attempt. A bounded worker
   may retry with a fresh observation under the same original authority.

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
The internal `admit_mcp_spawn` function composes approved private spawn ledger
provenance, the authoritative ready result, this read-only lookup, exact
identity matching and same-snapshot atomic append. It refuses before lookup
when there is no committed MCP proof, no Mail agent ID, or no ready launch.
It is not exposed as an MCP tool. The separately enabled gateway worker
invokes it only for an accepted, explicitly approved spawn receipt.

The later write uses the same SHA-256 snapshot. The helper does not
substitute for authorization or for the required independently attested
dashboard spawn record, and does not close Mail-side retirement races between
the final check and a later send.

## Explicit opt-in and runtime contract

- The fork's API 47 supports explicit legacy-only `mail_create_only`
  standalone spawn and canonical Mail `register_agent.create_only`. It
  refuses existing and concurrent identities, including case-equivalent
  and normalized legacy names. For both ordinary and create-only registration,
  a missing identity is rechecked under SQLite writer admission before insertion.
  Existing identities retain the authenticated update/null-token CAS path;
  create-only refuses unsupported DB dialects.
  The dry-run must explicitly
  echo the capability; the gateway never falls back to ordinary registration
  for an auto-admission preview.
- The operator must explicitly configure both spawn and message capabilities
  and `ORRERY_MCP_AUTO_REGISTER_SPAWNED=1` (or pass
  `--auto-register-spawned 1` with explicit `--spawn-dirs`,
  `--message-profile`, and `--message-helper-from` to service install).
  Leaving out the option disables auto-admission on installation. Read-only
  and fresh adapters reject the opt-in. Existing spawn/message approval and
  rate controls remain unchanged.
- A principal needs both spawn and message scopes to have a new launch
  enrolled. The preview explicitly describes the additional permission;
  local approval text also includes it. The approved preview pins the sender
  configuration and canonical connection instance. The same authority is
  required at commit and subsequent admission. A spawn-only principal can
  still use the unchanged normal launch path, without automatic admission.
- The gateway stores only the fixed private binding fields from the real
  create-only response (never from agent text or user arguments). Accepted
  launch and admission are separate outcomes. A private, durable ledger
  records the preview hash and exact receipt. Unknown spawn outcomes are
  never replayed or admitted. Ledger/lock files must be owner-private,
  regular, non-symlink files; ledger replacement and directory are fsynced.
- The opted-in `serve` process runs a bounded worker independent of read
  calls. It checks the retained dashboard readiness result and the canonical
  Mail helper, then atomically appends one peer. After a restart, it can
  reconcile a pending, exact receipt without spawning again. Reconciliation
  is limited to the 30-minute launch record window. Expiry or uncertain
  file I/O is not falsely reported as successful. The worker never sends a
  message. A full 16-peer profile refuses a new peer; no automatic eviction,
  replacement, or name-only lookup. To withdraw consent before a pending
  admission, disable the option before editing the operator profile.
- The spawn commit/replay response reports `recipient_registration`
  separately: pending, registered, not_registered, or unknown, with a fixed
  reason. Spawn status can report it once the ready record exactly matches
  the private Mail binding. It does not associate an old result with a
  same-name new launch or mutate recipient permissions. `orrery_status`
  reports the current authorized list; the message tool reads the profile
  when previewing and committing. Historical registration outcomes are not a
  guarantee that a peer remains authorized or active.

## Acceptance and production gates

The pinned gateway and real temporary Mail have an isolated E2E that uses a
synthetic Dashboard launch contract, restarts the gateway before readiness,
admits the exact created identity, rejects a different existing agent,
sends one message, observes read/ACK and receives a completion reply on the
same thread. Dashboard's own create-only receipt/launcher tests use a
separate fixture. This does not demonstrate a live provider launch or
ChatGPT-side write tool discovery.

Do not install or activate the feature until the fork changes are reviewed,
CI is green, the actual installed Dashboard and Mail support the create-only
contract, and the operator's private sender/profile have been checked.
Do not restart unrelated agent sessions or replace a full allowlist. A
production service update needs a safe rollback path and a separate
acceptance run. Installing the code alone does not grant auto-admission.

The pure-policy and owner-private file-operation unit tests can be run with:

    python -m pytest -q integrations/chatgpt_mcp/tests/test_chatgpt_gateway_auto_allowlist.py

The fresh/projectless adapter remains separately gated by fork #47 and
upstream #213 acceptance. No legacy identity, allowlist or credential may be
silently migrated into fresh Mail.
