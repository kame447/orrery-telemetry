# Sending instructions from ChatGPT to an existing agent

> 日本語: [chatgpt-mcp-message.md](chatgpt-mcp-message.md)

This adds tools to the [MCP connection for ChatGPT](chatgpt-mcp.en.md) that send an additional instruction to a running ORRERY agent **through ORRERY Mail**. It is **off by default**. It works only after the operator creates a dedicated sender on the Mac and explicitly allows the recipients.

## How it works

```text
ChatGPT ─ Secure MCP Tunnel ─ orrery-chatgpt-mcp (127.0.0.1)
                                  │ orrery_message_preview / commit / status
                                  ▼
                       DaemonRuntime (owner credential of the dedicated "orrery-daemon" sender)
                                  │ canonical send_message (allowed recipients only)
                                  ▼
                       ORRERY Mail ─▶ the recipient agent's inbox
```

- **The sender is a dedicated Mail identity.** It is the `program=orrery-daemon` registration the operator creates with `agentstack-daemon create --operator` ([deterministic daemons](daemon-agents.en.md)). The gateway never borrows an existing agent's owner token, never claims another sender name, never types into tmux and never touches the Mail database.
- **The recipient allowlist is the `peers` list of the daemon profile (`orrery-daemon-binding-v2`)**: 1–16 name / numeric Mail ID pairs written by the operator. ChatGPT cannot change it.
- **What is checked.** Right before every send, `DaemonRuntime` checks the Mail instance UUID over both the management socket and HTTP, the sender's own row, credential fingerprint and generation and `program`, and the recipient's numeric ID, project and retirement. The gateway also checks that the recipient's ID, program, project ID and registration time, and the sender and instance, are what the preview showed. A recycled name, the same name in another project, a retired agent or a missing agent is refused without sending.
- **Limits of those checks.** They run just before the send request and are not part of Mail's delivery transaction. Between the last check and the send, the recipient can re-register with its own owner token and change, for example, its program; that is not prevented. A replacement that changes the numeric ID is refused by the numeric-ID check right before the send.
- **Body checks.** A body or subject with control characters (C0/C1), bidirectional overrides, zero-width or other invisible format characters, tag characters, private-use characters or line/paragraph separators is refused at preview. Mail rewrites Markdown image references `![..](..)` (and reads the files they name), and the sender path cannot set importance, so image references are refused and importance is always `normal`. The subject and body ChatGPT shows are the subject and body that reach the inbox (checked end to end).

## Tools

| Tool | Kind | What it does |
| --- | --- | --- |
| `orrery_message_preview` | prepare | Returns recipient, subject, full body, sender, Mail IDs and `content_sha256`. Sends nothing |
| `orrery_message_commit` | write | Sends once, only with `preview_id`, `idempotency_key` and `content_sha256`. It is `accepted` only when Mail's receipt (message ID, sender, recipient, project, thread, subject) matches |
| `orrery_message_status` | read | The recorded outcome (accepted / rejected / unknown) and read / ACK in Mail |

`orrery_status` lists the allowed recipient names in `capabilities.message_recipients`.

The message arrives as follows, so the recipient can see where the instruction came from:

```text
<the instruction confirmed in ChatGPT>

---
Sent by the operator from ChatGPT through orrery-chatgpt-mcp as <sender>. orrery-mcp-ref: <16-hex reference>
```

The subject is `[ChatGPT] <subject>` and the thread is `orrery-mcp-<reference>`.

## The confirmation boundary (using it from a phone)

With the default `ORRERY_MCP_MESSAGE_APPROVAL=chat` you do not have to go back to the Mac each time. The boundary is the combination of:

1. Only recipients in the profile the operator wrote on the Mac.
2. A commit needs the preview's `content_sha256`, so nothing other than the preview ChatGPT showed can be sent. `orrery_message_commit` carries `destructiveHint: true`, so ChatGPT asks the user before running it.
3. An hourly cap (default 20, `ORRERY_MCP_MESSAGE_MAX_PER_HOUR`).
4. A send audit file, `message-audit.jsonl` (0600, no bodies).
5. Emergency stop: put a file at the profile's `pause_path` and later sends are refused.

To approve every message on the Mac, set `ORRERY_MCP_MESSAGE_APPROVAL=local`. The preview then returns an approval code, and `orrery-chatgpt-mcp approve <code>` shows the recipient, subject and full body (control characters escaped). The approval is bound to that preview and to the hash of that body.

## No duplicate sends

- Committing again with the same `idempotency_key` returns the recorded outcome and does not send again. One key cannot be used for a different message, and one preview can be sent under one key only. Key records never expire (they stay as long as the state directory does).
- The record, `message-ledger.json` (0600, no bodies), survives restarts and is serialised by a file lock across processes sharing the state directory. The sender profile lock is exclusive, so two sends never run at once.
- Previews live in memory only. After a gateway restart, preview again (a safe failure).
- If the answer is lost after the send request went out, or Mail's receipt is not the expected one, the outcome is recorded as **unknown**: it is not reported as success and it is never resent automatically. Only an explicit Mail refusal before storing (a file reservation conflict) is `rejected`.
- `orrery_message_status` looks in the dashboard's Mail view (`/api/edge-messages`) for a row with the sender and recipient names and exactly this message's thread and whole body (which contains a random reference), and records `accepted` if it finds one. A quotation, or a row with the same names and ID from another instance, does not match. Otherwise it stays unknown.
- If only the local record write fails after a send, the answer is `outcome_unrecorded` (do not send again). A send record that has not finished after 2 minutes is shown as unknown (`dispatch-unfinished`).

## What the states mean

| State | Meaning |
| --- | --- |
| `accepted` | Mail accepted the message into the recipient's inbox (with the Mail message ID) |
| `read` / `acknowledged` | The recipient marked it read / acknowledged it in Mail (when the dashboard can see it) |
| `rejected` | Not sent (with a reason code) |
| `unknown` | It is not known whether it was sent. Do not send again |

**This gateway cannot tell whether the agent has started or finished acting on the instruction.** Mail acceptance and read state are separate from the agent's TUI turn (general TUI input is Issue #32).

## Setting it up on the Mac (the operator does this)

1. Create the sender. This registers a new identity in the live Mail, so the operator runs it personally (do not have an agent run it):

   ```bash
   <Mail's Python> <ORRERY>/bin/agentstack-daemon create \
     --connection <connection file> --project-key <project> \
     --name ChatRelay --journal <journal> --operator
   ```

   See [deterministic daemons](daemon-agents.en.md) for the connection file and profile format.
2. In the profile (0600), list under `peers` the agents that may receive instructions, by name and numeric Mail ID taken from a canonical whois answer. `agentstack-daemon inspect --profile <profile>` verifies it.
3. Set the gateway environment and start it:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ORRERY_MCP_ENABLE_MESSAGE` | unset (off) | `1` turns it on |
| `ORRERY_MCP_MESSAGE_PROFILE` | none | Absolute path of the sender profile (required when on; setting it while off refuses to start) |
| `ORRERY_MCP_MESSAGE_APPROVAL` | `chat` | `chat` or `local` |
| `ORRERY_MCP_MESSAGE_MAX_PER_HOUR` | `20` | 1–200 |
| `ORRERY_MCP_MESSAGE_HELPER_ROOT` | the running checkout | The ORRERY checkout that provides `DaemonRuntime` (owned by you, not group/other writable) |

The gateway's Python does not need SQLAlchemy: the management-socket constants moved to `agentstack_mail.enrollment_protocol`.

Before importing from `ORRERY_MCP_MESSAGE_HELPER_ROOT`, the gateway checks all of the following: no directory above the checkout is writable by others (a root-owned sticky directory is fine); every directory and file in `integrations/codex_app/src` and `packages/agentstack_mail/src` is owned by you or root, not group/other writable, and not a symlink; both packages resolve to those locations; and no same-named package from elsewhere is already loaded. Otherwise it refuses without running anything.

### Logs

The gateway's records (ledger, audit, log) contain no bodies. ORRERY Mail's own tool log (on by default), however, records the arguments and results of every `send_message`, not only these, including the body. To keep bodies out of Mail's log as well, set `AGENTSTACK_MAIL_TOOLS_LOG_ENABLED=false` for Mail. Redacting bodies in Mail's log is a separate change.

### The launchd service

The read-only release pinned by `orrery-chatgpt-mcp service install` (PR #42) contains only the gateway source and passes no messaging settings. **Messaging stays off in the service.** Enabling it there is a separate change; until then, try it with a manually started gateway.

## Tests

- `integrations/chatgpt_mcp/tests/test_chatgpt_gateway_message.py`: contract tests with a fake sender (off by default, body checks, recipient changes, digest mismatch, key reuse, reconciling an unknown outcome, hourly cap, restart, concurrent commits, Mac approval, no bodies in the audit file).
- `integrations/chatgpt_mcp/tests/test_chatgpt_gateway_message_e2e.py`: starts a separate Mail process in a temporary directory with fictional agents only, sends through the gateway's HTTP MCP endpoint and checks that the inbox receives exactly one message. It also checks that nothing is sent to an unlisted recipient, after the recipient retires following the preview, while paused, or with Mail stopped. It never touches the live Mail, dashboard, launchd or tunnel.

Sending from the real ChatGPT through the tunnel has not been verified; it needs the user's own setup (Platform permissions, developer mode).
