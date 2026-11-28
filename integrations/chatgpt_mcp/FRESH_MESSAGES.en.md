# Phase two: explicit fresh Mail instructions (fork prototype)

The original `python -m orrery_chatgpt_mcp.readonly_server` entrypoint remains read-only and rejects write-enabling configuration. The separate `python -m orrery_chatgpt_mcp.fresh_server` entrypoint also defaults to read-only. No installer, daemon, launchd, UI, production service, public Mail tool, legacy credential migration or spawn behavior changes.

Until production fresh cutover is accepted, use a test Mail instance only. The [phase-two reply on upstream #395](https://github.com/gyroid-eth/orrery-telemetry/issues/395#issuecomment-7195231301) accepts a messaging PR within this isolated scope; it does not authorize production cutover.

## Fixed authority

An operator must explicitly prepare an existing fresh-mode protected client and a private policy file. The adapter calls upstream `FreshBoundClient.call('send_message', ..., request_id=...)`, the same protected implementation used by `agentstack_mail.fresh_client call-json`. It does not execute arbitrary shell commands, proxy arbitrary Mail tools or read/write the Mail database.

The policy fixes the Mail instance, sender ID and a finite recipient allowlist of stable IDs, expected names and `kind=to`. The policy allows 1–16 recipients. Startup and release preflight refuse 17 or more without deleting, truncating or replacing existing entries. Each instruction has one recipient. Tool arguments cannot select contexts, credentials, endpoints, senders, projects or request UUIDs. The Mail write transaction remains responsible for current authentication, target identity, retirement and contact policy. Preview-time whois is not authorization to bypass that transaction.

This mode delegates send authority to the connecting client. ChatGPT confirmation and the preview/commit sequence are not server-verified human approval. Any caller with the private URL and Tunnel access shares this authority. The recipient allowlist restricts sends only: existing fresh read tools still expose the selected Mail instance's full permitted roster.

## Operator preparation

Use confirmed context, instance and recipient identities. Do not switch a running legacy installation to fresh, register production identities or guess values merely to pass startup checks. Use a development environment containing this checkout's Mail package and dependencies; never modify a production service venv.

The 0600 owner-owned policy has exactly this shape. All example values must be replaced with identities actually verified by the operator. Context preparation is an existing trusted fresh operation, not an MCP tool.

```json
{
  "kind": "orrery-mcp-fresh-message-policy-v1",
  "context_path": "/absolute/private/fresh/clients/confirmed-client-uuid/context.json",
  "mail_instance_id": "0123456789abcdef0123456789abcdef",
  "sender_agent_id": 1,
  "recipients": [
    {"target_agent_id": 2, "expected_target_name": "ConfirmedRecipient", "kind": "to"}
  ]
}
```

Symbolic/hard links and duplicate JSON keys are rejected. Select a dedicated absolute canonical 0700 state directory; do not silently reuse an existing service's state or URL for writes. The following manual example avoids the existing 8790/8791 listeners. The Dashboard must already be the confirmed fresh Dashboard, not an automatically converted production deployment.

```sh
export PYTHONPATH="$PWD/integrations/chatgpt_mcp/src:$PWD/packages/agentstack_mail/src"
export ORRERY_MCP_MODE=fresh
unset ORRERY_MCP_PROJECT_KEY
export ORRERY_MCP_PORT=8792
export ORRERY_MCP_STATE_DIR=/absolute/private/dedicated-mcp-state
export ORRERY_MCP_FRESH_MESSAGE_POLICY=/absolute/private/message-policy.json
export ORRERY_MCP_DASHBOARD_URL=http://127.0.0.1:8770
export ORRERY_MCP_ENABLE_SPAWN=0
ORRERY_MCP_ENABLE_MESSAGE=0 python -m orrery_chatgpt_mcp.readonly_server pair --show-url
export ORRERY_MCP_ENABLE_MESSAGE=1
python -m orrery_chatgpt_mcp.fresh_server check
python -m orrery_chatgpt_mcp.fresh_server serve
```

Check does not send. Serve validates the fixed authority and Dashboard instance before opening its loopback listener. Stop with Ctrl+C; disable by stopping the process and restarting with ENABLE_MESSAGE=0. A file edit alone does not alter the running process's grant. Policy/context changes cause a refusal before sends, rather than silently adopting new authority. Service installation is out of scope.

Before connecting, verify Tunnel organization/workspace, sharing, Read/Use access and private URL custody. Verify the Mail process's effective tool/body logging settings and owner-private Mail/Tunnel logs. This adapter does not log request bodies or credentials, but cannot automatically secure other processes' logs. Existing tunnels are not changed.

## Preview, commit and evidence

The enabled gateway adds `orrery_message_preview`, `orrery_message_commit` and `orrery_message_status` to the three read tools. Preview accepts exactly idempotency_key, mail_instance_id, target_agent_id, expected_target_name, subject and body_md. It returns the complete message, recipient, digest, preview ID, request UUID and five-minute expiry. The UTF-8 limits are 200 bytes for the subject and 65536 for the body. Empty values and control characters are rejected except body LF/TAB. Preview persists local state, so its readOnlyHint is false, even though it sends no Mail.

Commit accepts only idempotency_key, preview_id and content_digest. Different content cannot reuse a key. Before transport, an owner-private SQLite transaction durably claims the request. Concurrent callers, separate processes and restarts cannot send a claimed request again. UUIDs are deterministic from Mail instance, sender ID and key hash, so an accidental different adapter state still reaches upstream's same idempotency boundary. This does not make deleting the ledger safe. Every ledger read validates the saved message and recipient against the fixed policy, recomputes its digest and the request UUID derived from the key, and checks preview format, state and expiry. Inconsistent evidence at operation entry raises message_state_invalid before any send or receipt lookup. Re-reads during an operation are also checked; if inconsistency appears then, transport may already have occurred, so preserve the evidence and do not resend. Accepted records require the exact receipt projection with the correct sender and instance; refused records require a fixed-format refusal code. Invalid state/observation values and extra saved receipt fields are refused. These checks detect corrupted or misplaced private records; they are not a signature against arbitrary changes by the same OS user.

Acceptance requires the protected send response's message ID, matching instance/sender/recipients, decision_only=false, notification_pending, thread and timestamp. Empty or decision-only responses are not accepted. The upstream send atomically stores the body, recipients and request receipt; a valid stored-send response need not echo the body. Before returning a previous send outcome for the same request UUID, this fork also verifies the stored subject, body, importance, ACK requirement and topic against the matching intent, and the thread against its saved receipt. An inconsistent result is refused and remains unknown in MCP. Existing intent hashes and valid receipt formats are unchanged; a recipient retiring after a completed send does not erase its historical acceptance. Acceptance is not execution: execution_status remains unobserved. Commit/status omit message bodies, credentials and local paths.

Status returns settled local evidence and reconciles only unknown/sending requests using the [authenticated receipt-read extension](../../docs/fresh-message-receipts.en.md). It first compares the protected saved request with the complete preview, so another body under the same UUID cannot be mistaken for success. It persists observations and verified state in the adapter's own SQLite ledger, so this tool has readOnlyHint=false. Settling accepted/refused requires verified evidence; not_observed/unavailable observations are also stored. Concurrent send completion cannot downgrade settled state to unknown. Mail is read only: this tool never calls replay or send.

Receipt lookup is a fork extension. An unsupported older backend, outage, revoked authority or mismatched saved content leaves the request unknown. Not_observed only describes the read snapshot; an in-flight send may still commit, so it never authorizes resending. receipt_observation distinguishes these cases. Agent execution remains unobserved. Expired previews are not refreshed and new keys are never automatically chosen. At 10000 records the journal refuses new previews rather than deleting unknown/accepted/refused evidence.

Both the adapter journal and the protected client's requests store message bodies privately. Preserve their 0700/0600 protection; credentials are not copied into the adapter journal. Do not publish or casually delete these files.

If inconsistent ledger evidence stops an operation, preserve the ledger and protected client requests for investigation. Do not recover by deleting evidence, choosing another key or truncating an oversized policy. Valid existing ledgers retain their format. An older policy with more than 16 recipients refuses startup until the operator explicitly reviews its authority.

## Disabled-state checks and scope of tests

The original entrypoint or ENABLE_MESSAGE=0 must expose only the three read tools. Status reports read_only=true, spawn_enabled_on_server=false and message_enabled_on_server=false; directly calling commit returns unknown_tool. An unauthenticated 404 or healthz alone is not proof of disabled authority. Enabled status identifies delegated authority, sender and allowed recipients; healthz reports read_only=false. Spawn is always disabled.

Tests use temporary fresh roots, dynamic local ports, synthetic registered agents and isolated HOME. They exercise real fresh Mail HTTP/storage, exact bodies/recipients and request UUIDs, independent restart, lost responses, retirement, denied contact, corrupt identity fail-closed, and existing read/Host/Origin/path behavior. No production service or agent is used. An actual MCP SDK also initializes, discovers tools and completes preview/commit/status over the isolated HTTP gateway. This does not validate hosted ChatGPT/Tunnel delivery or actual agent execution.

Isolated launchd tests independently check removal of their dedicated job and every observed PID even if shutdown fails or times out. Tests with listening ports or logs also check port closure and log privacy. They never retry an uncertain shutdown, and preserve the original test failure while adding fixed observation-failure codes. These failure-path regressions run in ordinary CI without starting macOS jobs. Actual launchd acceptance remains a separate, explicitly opted-in test.
