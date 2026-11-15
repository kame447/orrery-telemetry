# Phase two: explicit fresh Mail instructions (fork prototype)

The original `python -m orrery_chatgpt_mcp` entrypoint remains read-only and rejects write-enabling configuration. The separate `python -m orrery_chatgpt_mcp.fresh_server` entrypoint also defaults to read-only. No installer, daemon, launchd, UI, production service, public Mail tool, legacy credential migration or spawn behavior changes.

## Fixed authority

An operator must explicitly prepare an existing fresh-mode protected client and a private policy file. The adapter calls upstream `FreshBoundClient.call('send_message', ..., request_id=...)`, the same protected implementation used by `agentstack_mail.fresh_client call-json`. It does not execute arbitrary shell commands, proxy arbitrary Mail tools or read/write the Mail database.

The policy fixes the Mail instance, sender ID and a finite recipient allowlist of stable IDs, expected names and `kind=to`. Each instruction has one recipient. Tool arguments cannot select contexts, credentials, endpoints, senders, projects or request UUIDs. The Mail write transaction remains responsible for current authentication, target identity, retirement and contact policy. Preview-time whois is not authorization to bypass that transaction.

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
ORRERY_MCP_ENABLE_MESSAGE=0 python -m orrery_chatgpt_mcp pair --show-url
export ORRERY_MCP_ENABLE_MESSAGE=1
python -m orrery_chatgpt_mcp.fresh_server check
python -m orrery_chatgpt_mcp.fresh_server serve
```

Check does not send. Serve validates the fixed authority and Dashboard instance before opening its loopback listener. Stop with Ctrl+C; disable by stopping the process and restarting with ENABLE_MESSAGE=0. A file edit alone does not alter the running process's grant. Policy/context changes cause a refusal before sends, rather than silently adopting new authority. Service installation is out of scope.

Before connecting, verify Tunnel organization/workspace, sharing, Read/Use access and private URL custody. Verify the Mail process's effective tool/body logging settings and owner-private Mail/Tunnel logs. This adapter does not log request bodies or credentials, but cannot automatically secure other processes' logs. Existing tunnels are not changed.

## Preview, commit and evidence

The enabled gateway adds `orrery_message_preview`, `orrery_message_commit` and `orrery_message_status` to the three read tools. Preview accepts exactly idempotency_key, mail_instance_id, target_agent_id, expected_target_name, subject and body_md. It returns the complete message, recipient, digest, preview ID, request UUID and five-minute expiry. The UTF-8 limits are 200 bytes for the subject and 65536 for the body. Empty values and control characters are rejected except body LF/TAB. Preview persists local state, so its readOnlyHint is false, even though it sends no Mail.

Commit accepts only idempotency_key, preview_id and content_digest. Different content cannot reuse a key. Before transport, an owner-private SQLite transaction durably claims the request. Concurrent callers, separate processes and restarts cannot send a claimed request again. UUIDs are deterministic from Mail instance, sender ID and key hash, so an accidental different adapter state still reaches upstream's same idempotency boundary. This does not make deleting the ledger safe.

Acceptance requires the protected send response's message ID, matching instance/sender/recipients, decision_only=false, notification_pending, thread and timestamp. Empty or decision-only responses are not accepted. The upstream send atomically stores the body, recipients and request receipt; a valid stored-send response need not echo the body. Acceptance is not execution: execution_status remains unobserved. Commit/status omit message bodies, credentials and local paths.

Status only reads the local journal. It never calls Mail, replay or send. Lost responses, timeout, interrupted claims, ambiguous refusals and incomplete results remain unknown. An exception after dispatch is not proof that nothing was stored. Do not automatically choose a new key to resolve uncertainty.

The current upstream protected client has no published read-only receipt lookup by request UUID. Replay may perform a previously unaccepted send, so it cannot hide inside a read-only status operation. Automatic server reconciliation of unknown results is not implemented and requires a separately agreed upstream receipt-read contract. Expired previews are not refreshed. At 10000 records the journal refuses new previews rather than deleting unresolved/accepted evidence.

Both the adapter journal and the protected client's requests store message bodies privately. Preserve their 0700/0600 protection; credentials are not copied into the adapter journal. Do not publish or casually delete these files.

## Disabled-state checks and scope of tests

The original entrypoint or ENABLE_MESSAGE=0 must expose only the three read tools. Status reports read_only=true, spawn_enabled_on_server=false and message_enabled_on_server=false; directly calling commit returns unknown_tool. An unauthenticated 404 or healthz alone is not proof of disabled authority. Enabled status identifies delegated authority, sender and allowed recipients; healthz reports read_only=false. Spawn is always disabled.

Tests use temporary fresh roots, dynamic local ports, synthetic registered agents and isolated HOME. They exercise real fresh Mail HTTP/storage, exact bodies/recipients and request UUIDs, independent restart, lost responses, retirement, denied contact, corrupt identity fail-closed, and existing read/Host/Origin/path behavior. No production service or agent is used. This does not validate hosted ChatGPT/Tunnel delivery, actual agent execution or server reconciliation of unknown results.
