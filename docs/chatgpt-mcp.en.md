# MCP connection for ChatGPT (local to your Mac)

> 日本語版: [chatgpt-mcp.md](chatgpt-mcp.md)

`orrery-chatgpt-mcp` is a small gateway that lets a ChatGPT custom MCP connection read ORRERY state and, within limits you set, start new agents. It replaces the multi-hop route through Desktop Commander and localhost HTTP with ORRERY-specific MCP tools.

**This repository provides only the server side.** Creating the connection in ChatGPT, tunnel permissions in the OpenAI Platform, and ChatGPT developer mode are steps the user takes. Starting the gateway does not make it "installed" in ChatGPT.

## Layout and boundary

```text
ChatGPT ──(OpenAI)── Secure MCP Tunnel ◀── tunnel-client (Mac, outbound HTTPS only)
                                               │ http://127.0.0.1:8790/mcp/<pairing secret>
                                               ▼
                                  orrery-chatgpt-mcp (listens on 127.0.0.1 only)
                                               │ fixed allowlist of 6 routes
                                               ▼
                                  dashboard http://127.0.0.1:8770 (never exposed)
```

- The service, data and action APIs all stay on this Mac. No public HTTPS server, cloud hosting, own OAuth authorization server, or Cloudflare-style exposure is built.
- ChatGPT traffic goes through OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). The Mac only opens outbound connections to `api.openai.com:443`. **Tool requests and responses pass through OpenAI, so this is not offline.** Whatever is shown to ChatGPT (agent names, task beginnings) becomes part of the conversation.
- The gateway refuses any non-loopback bind (no `0.0.0.0`). The dashboard (8770) and ORRERY Mail (18765) stay unexposed as before.

## Owner controls

| Control | What it does |
| --- | --- |
| Pairing secret | The only MCP entrance is `/mcp/<43-character secret>`. `orrery-chatgpt-mcp pair` writes it to `~/.agentstack/runtime/chatgpt-mcp/pairing.json` (0600, never overwritten); give it only to the tunnel-client configuration. Other paths return 404 and guesses are rate limited. `pair --rotate` revokes the old URL immediately, even in a running gateway |
| Host / Origin | Host must be `127.0.0.1` / `localhost` / `[::1]` with the real port. Any request with an Origin (a browser) is refused, which blocks DNS rebinding and browser CSRF |
| Approval on the Mac | Launch is "preview → `orrery-chatgpt-mcp approve <code>` on the Mac → commit". `approve` shows the full task, directory and model (control characters escaped); the approval is bound to that preview and to the hash of the task text shown. The model resolved at preview is pinned for the launch. Nothing over MCP can create an approval. Tasks containing control or bidi-override characters are refused at preview |
| Launch scope | Off by default. When enabled: only operator-registered directory aliases, only `claude` / `codex`, standalone (no parent), worktree isolation by default. `tools`, `base`, `parent` and similar fields are refused |
| Duplicate prevention | Commit requires a caller `idempotency_key`. A repeat returns the recorded outcome and never starts a second agent. Records live in `spawn-ledger.json` (0600) and survive restarts |
| Unknown outcome | If the answer is lost after sending, the gateway records `unknown`, never success, and reconciles through `orrery_spawn_status` instead of resending |
| Data minimisation | Agent rows carry a fixed field list. Pane titles, commands, paths, transcripts, Mail internals and launcher log tails are dropped. Text is clipped to 300 characters and labelled untrusted data |
| Logs | One line per request (subject, registered tool name, outcome). No arguments, secrets or tokens. Dashboard error text can contain paths and diagnostics, so ChatGPT gets only `has_error` or a code |
| Connection handling | Connections close after any refusal. Duplicate Content-Length and any Transfer-Encoding are refused. 15-second read deadline and at most 32 concurrent requests. One gateway per state directory |
| Never borrowed | ORRERY Mail owner tokens, parent/child credentials and the dashboard operator capability |

Programs running as the same macOS user are inside this boundary ([local HTTP trust boundary](local-http-security.md)). A local program that can read the pairing file, or a local agent that can run `approve`, is not kept out.

## Tools

| Tool | Kind | Purpose |
| --- | --- | --- |
| `orrery_status` | read | Dashboard reachability, API generation, and what this connection can do |
| `orrery_list_agents` | read | Agents with running state, activity (work / wait / question / ask), model, pending deliveries |
| `orrery_get_agent` | read | One agent |
| `orrery_spawn_status` | read | launching / ready / failed for a recent launch |
| `orrery_spawn_preview` | launch (dry run) | Validates through the dashboard `dry_run`; returns `preview_id` and an approval code. Starts nothing |
| `orrery_spawn_commit` | write | Starts an approved preview |

Instructing or messaging an existing agent is **not in this version**: ORRERY has no operator API that sends Mail without borrowing an agent's owner token, and tmux keystrokes are not used. `orrery_status` reports this as `message_existing_agent: false`. There is no stopping of working agents (EXIT / kill), no arbitrary command execution and no transparent dashboard proxy.

ChatGPT plans differ for write MCP tools (OpenAI help as of 2026-10: Pro has read/fetch; full write is a Business / Enterprise / Edu beta). The gateway never guesses your plan. The operator decides with `ORRERY_MCP_ENABLE_SPAWN`; if your plan cannot use write tools, use the read tools only.

## Steps on the Mac

1. Create the pairing secret once. The printed URL contains the secret; do not share it.

   ```sh
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp pair
   ```

   `AGENTSTACK_PYTHON` selects Python 3.11+. The default pairing mode needs only the standard library (PyJWT is needed only for the optional OAuth mode).

2. Start the gateway. Read-only use needs no settings.

   ```sh
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp serve
   ```

   To allow launches, register directory aliases and enable them:

   ```sh
   ORRERY_MCP_ENABLE_SPAWN=1 \
   ORRERY_MCP_SPAWN_DIRS="notes=/Users/you/notes,web=/Users/you/src/web" \
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp serve
   ```

3. Install the official [tunnel-client](https://github.com/openai/tunnel-client), create a tunnel, and give it the loopback URL from step 1. Check the exact flags with `tunnel-client help quickstart`.

   ```sh
   tunnel-client init --profile orrery --tunnel-id <tunnel_...> --mcp-server-url "$(~/orrery-telemetry/bin/orrery-chatgpt-mcp url)"
   tunnel-client doctor --profile orrery --explain
   tunnel-client run --profile orrery
   ```

4. Enable developer mode in ChatGPT and create a custom MCP connection with Connection: Tunnel and authentication "No authentication" (ChatGPT itself sends no credential; the entrance secret and the approval on the Mac carry the owner check).

5. When ChatGPT asks to start an agent, review and approve it on the Mac:

   ```sh
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp pending
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp approve K7P2QX9M   # shows the launch; approves only after you type yes
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp deny K7P2QX9M      # discard it
   ```

### What the user has to do

- Get Tunnels permissions in the OpenAI Platform organization (creators need Read + Manage; whoever runs tunnel-client or creates the connection needs Read + Use), granted by an org owner or RBAC admin.
- Enable ChatGPT developer mode (Enterprise / Edu also need workspace admin permission). The tunnel must be associated with the target ChatGPT workspace.
- Provide tunnel-client's runtime API key (`CONTROL_PLANE_API_KEY`) and keep it out of the repository.
- Check that your ChatGPT plan supports write tools.

If Secure MCP Tunnel is not available to your account, do not expose the gateway another way. It then remains usable by local MCP clients but not reachable from ChatGPT.

## Failure answers

| Situation | Result |
| --- | --- |
| Path without or with a wrong secret | HTTP 404 (429 after too many) |
| Non-loopback Host, any Origin | HTTP 403 |
| Dashboard down or malformed answer | Tool result `isError: true`, `dashboard_unavailable`; `orrery_status` shows `reachable: false` |
| Launch disabled or out-of-policy arguments | `unknown_tool` / `invalid_arguments` |
| Commit before approval | `approval_required` (the preview is not consumed) |
| No answer during or after sending | `outcome_unknown`; a repeat with the same key reconciles and does not resend ("not sent" only when no connection was made) |
| Invalid JSON-RPC, a response message, or a batch | HTTP 400 |

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `ORRERY_MCP_PORT` | `8790` | Listening port (127.0.0.1) |
| `ORRERY_MCP_DASHBOARD_URL` | `http://127.0.0.1:8770` | Loopback dashboard only |
| `ORRERY_MCP_STATE_DIR` | `~/.agentstack/runtime/chatgpt-mcp` | Pairing, approvals, ledger |
| `ORRERY_MCP_ENABLE_SPAWN` | `0` | `1` exposes the launch tools |
| `ORRERY_MCP_SPAWN_DIRS` | none | Comma-separated `alias=/absolute/path` |
| `ORRERY_MCP_SPAWN_APPROVAL` | `local` | `chat` skips approval on the Mac and relies on ChatGPT's confirmation only (not recommended) |
| `ORRERY_MCP_RATE_PER_MINUTE` | `60` | Requests per minute |

### Optional: OAuth mode

`ORRERY_MCP_AUTH=oauth` replaces pairing with verification of OAuth 2.1 access tokens issued by an external authorization server (issuer / audience / expiry / signature / scopes `orrery.read` and `orrery.spawn` / allowed `sub`). The gateway never becomes an authorization server. The local-first setup does not use it; it remains for using an existing IdP through the tunnel. Settings: `ORRERY_MCP_RESOURCE`, `ORRERY_MCP_ISSUER`, `ORRERY_MCP_JWKS_URL` or `ORRERY_MCP_JWKS_FILE`, `ORRERY_MCP_ALLOWED_SUBJECTS`.

## Verification

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests
```

Covers unauthenticated access, wrong scopes, forged tokens, Host/Origin, injection, duplicate launches, unknown outcomes, a stopped dashboard and E2E with the official MCP SDK client. A connection from ChatGPT itself needs the user's setup and is not covered by these tests.
