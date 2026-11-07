# Project idle watch (design note)

> 日本語版: [idle-watch.md](idle-watch.md)

The dashboard can wake a project's parent once when no agent of that project is working. It is opt-in and off by default.

## Findings

- **No "input box is empty" resume exists in this repository.** A search of `dashboard/`, `bin/`, `hooks/`, daemon and persistent-agent code found none. It lives outside (Laplance) and is not touched.
- **`act_state`** (`work` / `wait` / `ask`) is read from one tmux `capture-pane` per running session (`_agent_runtime`, `_parse_runtime`, cached 4.5 s) and served in `/api/agents`. `harness_activity.parse_activity` covers only dsh/Hermes. `None` means "could not tell", never idle. Rows carry no per-row timestamp; the payload has `ts`.
- **`project_key` is `null` on some rows** (e.g. a parent started outside the project). Membership is therefore: running rows whose `project_key` equals the project, plus the configured parent by name. Rows with an unknown project are ignored.
- **Canonical wake paths:** an ORRERY Mail `send_message` to the parent (the mail watcher delivers it); for a parent whose session is gone, `do_jump` (the same path as `agentstack-resume`). Nothing types into tmux.

## Behavior

Each sample is one of: `busy` (any `work`), `idle` (all `wait`), `blocked` (no `work`, at least one `ask`), `unknown` (any other/missing state, no members, or stale/failed fetch). Only `idle` and `blocked` can act, after `samples` consecutive samples spanning `seconds`.

- `idle`: one Mail to the parent from `sender` (marked `source: idle-watch`, with each agent's state and seconds in that state). If the parent is not running, it is resumed instead. Resends follow `resend_after` × 2^k (cap `resend_cap`), at most `max_resends` times.
- `blocked`: no resume. One desktop notification (macOS `osascript`) and a log line per blocked interval.
- Any change of class (work returns, ask appears or clears, unknown) ends the interval and re-arms it. State is in memory; a dashboard restart re-judges from scratch.

## Configuration

`$AGENTSTACK_RUNTIME_DIR/idle-watch.json`; a missing file means disabled. Defaults: `samples` 3, `seconds` 60, `poll` 10, `stale` 30, `resend_after` 600, `max_resends` 3, `resend_cap` 3600.

```json
{"projects": {"/abs/project": {"enabled": true, "parent": "ParentName", "sender": "WatcherName"}}}
```

`sender` is an already-registered Mail identity whose owner token is in the runtime directory; the watcher does not register one. Without it the Mail step logs a warning and sends nothing.

## Dry run

`bin/agentstack-idle-watch --project KEY --parent NAME [--samples N --seconds T --poll S --iterations K]` samples the live `/api/agents`, logs the class and per-agent states, and prints what it would send. It never sends or resumes.
