# Project idle watch (design note)

> 日本語版: [idle-watch.md](idle-watch.md)

When no agent of a project is working, the dashboard notifies the running parent over ORRERY Mail. It is opt-in and off by default. It never resumes a parent. It is an aid; it does not guarantee the "parent hands out the next task when a child finishes" workflow.

## Findings

- **No "input box is empty" resume exists in this repository.** A search of `dashboard/`, `bin/`, `hooks/`, daemon and persistent-agent code found none. It lives outside (Laplance) and is not touched.
- **`act_state`** (`work` / `wait` / `ask`) is read from one tmux `capture-pane` per running session (`_agent_runtime`, `_parse_runtime`, cached 4.5 s) and served in `/api/agents`. `harness_activity.parse_activity` covers only dsh/Hermes. `None` means "could not tell", never idle. Rows carry no per-row timestamp; the payload has `ts`.
- **`project_key` is `null` on some rows** (a parent started outside the project, a name registered in several projects, ...). Membership: rows whose `project_key` equals the project; the configured parent (by name) must be one of them.
- **Canonical wake path:** an ORRERY Mail `send_message` to the parent (the mail watcher delivers it). Nothing types into tmux.

## Behavior

Each sample is one of: `busy` (any member `work`), `idle` (all `wait`), `blocked` (no `work`, at least one `ask`), `unknown`. Only `idle` and `blocked` can act, after `samples` consecutive samples spanning `seconds`.

The sample is `unknown`, and nothing is sent (fail-closed), when:

- the parent row is absent or not running (a stopped or retired parent is never resumed);
- the parent's row does not carry this project's `project_key` (another project, or `null` because the name is unregistered or registered in several projects);
- a member row is `running: false` (liveness unknown) or `retired_but_alive`;
- a Mail-registered row with `project_key: null` (e.g. a name in several projects) is anything but a running `wait`, since it may be a child of this project; tmux sessions not registered in ORRERY Mail are ignored;
- an `act_state` is missing or not work/wait/ask, the fetch failed, or `ts` is older than `stale` seconds or in the future.

Per class:

- `idle`: one Mail to the parent, sent from the identity of the parent's verified child that worked last. Verified means: tmux `PARENT_AGENT` equals the parent; the child state's name, project and parent match; the runtime owner token equals the child state's token (the tmux generation, registered agent_id and program checks of provider-stop notices are not done). Right before sending it re-reads `/api/agents` and sends only if the same members are still `idle`. Subject and body say it is an automated dashboard notice, not written by the sending child, and list each agent's state and seconds in that state.
- Resends: only after a send that returned `ok`, while the same idle continues, every `resend_after` × 2^k (cap `resend_cap`), at most `max_resends` times. By default that is one send plus at most 3 resends. If the result is unknown (timeout etc.), rejected, no child verifies, the state changed before sending, or an exception occurred, nothing more is sent automatically.
- The send budget is restored only when a member is seen in `work`; `unknown` or `blocked` samples do not restore it.
- `blocked`: nothing is sent. One desktop notification (macOS `osascript`) and a log line per blocked interval.
- State is in memory; a dashboard restart re-judges from scratch.

## Configuration

`$AGENTSTACK_RUNTIME_DIR/idle-watch.json`; a missing file means disabled. It is read once when the dashboard starts; changes need a dashboard restart. It runs only in legacy mode, not in global or fresh mode.

Defaults: `samples` 3, `seconds` 60, `poll` 10, `stale` 30, `resend_after` 600, `max_resends` 3, `resend_cap` 3600. All values are integers in these ranges: `samples` 1–1000, `seconds` 0–86400, `poll` 1–3600, `stale` 1–3600, `resend_after` 1–86400, `max_resends` 0–10, `resend_cap` 1–86400. A project with an out-of-range, fractional, string, boolean or NaN value, or an empty `parent`, is disabled with a logged warning. If `projects` is not an object, the whole feature is disabled.

```json
{"projects": {"/abs/project": {"enabled": true, "parent": "ParentName"}}}
```

No identity is created.

## Limitations

- If the parent has stopped, nothing happens; a person resumes the parent.
- It never fires when the parent is not registered in this project, or when the parent's name is also registered in another project.
- The decision depends on `act_state` read from the tmux screen; a project with an agent whose screen cannot be classified never fires.
- State transitions and the send call are tested with Mail mocked; real Mail delivery has not been verified.

## Dry run

From a source checkout (the installer does not copy this tool): `bin/agentstack-idle-watch --project KEY --parent NAME [--samples N --seconds T --poll S --iterations K]` samples the live `/api/agents`, logs the class and per-agent states, and prints what it would send. It never sends or resumes. Out-of-range values are rejected.
