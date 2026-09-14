# Agentstack Hooks

`settings.template.json` is a Tier1 safe-global-minimal Claude Code settings
fragment. The installer should replace `__AGENTSTACK_HOOKS_DIR__` with the
installed hooks directory, typically `${HOME}/.agentstack/hooks`, then merge the
entries with a JSON parser.

The template intentionally includes only safe global hooks: registration gating,
file-reservation gating and release, registration marking, and no-op-safe
SessionStart title/metadata hooks. Its SessionEnd hook only releases file
reservations; it never retires an identity. It does not install
`cleanup-child-agent.sh` as a SessionEnd hook because that script can retire an
agent; SessionEnd may run during crash/resume flows, so irreversible actions do
not belong there.

## Invocation project resolver (incremental migration API)

`project-context.sh` exposes the side-effect-free shell function
`agentstack_resolve_invocation_project_key TARGET [EXPLICIT_KEY [NON_GIT_FALLBACK]]`
and the matching `resolve-invocation-project-key` subcommand. TARGET must be an
existing directory. The result is printed without exporting context, writing
runtime state, registering an agent, or contacting Mail/tmux.

The selection order is a nonempty explicit per-invocation key, the target's Git
repository identity, a caller-supplied non-Git fallback, and finally the physical
target directory. Main and linked worktrees share the common Git directory: the
usual `.git` layout uses its parent checkout; bare, separate-git-dir and submodule
layouts use the physical common directory. Independent clones remain separate.
Explicit keys are path-normalized, not collapsed to a repository: logical names
stay literal, and an explicitly selected linked-worktree key remains that key.
Relative explicit/fallback paths are interpreted from the caller's working
directory, so callers should pass absolute paths when they intend path keys.

The new selector never reads installed `env.sh`, ambient `AGENTSTACK_PROJECT_KEY`
or `PROJECT_KEY`, or an inherited context marker. A launcher must obtain any
non-Git configuration fallback through its documented configuration reader and
pass it explicitly. Git probes ignore inherited `GIT_DIR`, `GIT_WORK_TREE` and
`GIT_COMMON_DIR`. An invalid target, missing Git executable, unexpected Git probe
failure, or unresolved repository metadata returns an error rather than selecting
a fallback. Explicit selection does not require Git discovery.

This is a top-level selection API, not an ownership/authentication check. A
caller must not forward an inherited delegated key as an explicit override just
because a context marker exists. Top-level launchers wrap a freshly resolved
context with `agentstack_build_invocation_transport` and pass it to bootstrap
as an explicit argument. Bootstrap calls `agentstack_validate_invocation_transport`
against its actual target. The same JSON copied through the environment has no
authority.

The existing `agentstack_resolve_project_key`, `agentstack_resolve_protected_roots`,
`agentstack_installed_env_value` and their existing CLI commands keep their old
behavior for consumers not yet migrated. Top-level launchers, standalone Codex
and Gemini bootstrap, registration/reregistration, SessionStart identity, and
session-index writing now use the workspace context and ownership APIs. Child
spawn/reservation/handoff/cleanup, watchers, Dashboard and installer-generated
instructions remain separate migrations; see `docs/launchers.md`.

For a library call, source `hooks/project-context.sh` and call the function above.
For a read-only command-line check, run
`bash hooks/project-context.sh resolve-invocation-project-key /absolute/target`.
Pass an empty second argument before a non-Git fallback when there is no explicit
key. Neither form starts agents or alters a running installation.

## Invocation workspace context (next migration layer)

`agentstack_resolve_invocation_context TARGET [EXPLICIT_KEY [NON_GIT_FALLBACK]]`
and `bash hooks/project-context.sh resolve-invocation-context ...` return one JSON
object on success. `project_key` follows the selection order above;
`repository_key` is the inspected canonical Git identity, or null for a verified
non-Git directory. `work_dir` is the physical invocation target, including any
subdirectory. `worktree_root` is the current checkout's physical root, or null
for non-Git. `protected_roots` is an array containing that worktree root, or the
physical non-Git target. A logical or explicitly overridden key is a namespace,
not a pathname to protect; neither an override nor a non-Git fallback changes
the repository binding or the workspace roots.

The context API and the key-only API share repository inspection and selection
rules. Unlike key-only explicit selection, a complete context always inspects
Git, even with an explicit key. Broken metadata, Git execution failure, failure
to inspect a working tree, or a mismatch between its identity and the target
returns nonzero with no JSON. Bare repositories and targets inside Git metadata
have identity but no usable working-tree context and are rejected by this API;
the key-only API continues to support their identity queries. A submodule or a
separate-git-dir checkout protects its working tree, not its metadata directory.
Linked worktrees share repository identity but retain their own work_dir and
protected root. Default roots do not enumerate other linked worktrees.

Installed env and ambient project keys, repository markers and protected roots
are never inputs. The function does not change the caller's cwd or environment,
set AGENTSTACK_PROJECT_CONTEXT, contact Mail/tmux, or write registration/session
state. JSON is data, not shell code: consumers must decode it, not eval it. A
later adapter to the legacy colon-delimited roots variable must reject paths it
cannot represent rather than silently splitting a single root.

These are default workspace protection roots, not an authorization credential
or a filesystem access allowlist. Additional configured roots and delegated or
reserved-child ownership validation belong to their consumer migration. A
validated child context must not be reselected as a top-level invocation, and a
bare AGENTSTACK_PROJECT_CONTEXT=1 marker is never proof of ownership.

The context layer itself remains read-only. Its top-level launcher adapter
validates representability before exporting a complete tuple, does not eval
JSON, and never treats a context marker as delegated ownership proof.

## Registration ownership and session bindings

`bin/lib/agentstack-register.sh` publishes
`runtime/agent_owner_<name>.json` with mode `0600` only after registration
succeeds. It binds the project namespace to the actual Git repository (shared
across linked worktrees) or a non-Git physical root and to a SHA-256 digest of
the owner token. The raw token remains in `agent_token_<name>`. A separate
`.pending` claim prevents local races but is not accepted as ownership and is
removed on failure.

Readers re-resolve the actual target. A contradictory strong record, an
independent clone, another repository, or a token digest mismatch fails before
`ensure_project` or token transmission. Legacy `child-agents/<name>.json` state
is accepted only for the same Git repository and only after token-authenticated
`whois`; successful registration upgrades it. Cross-repository or ambiguous
legacy state requires an explicit relaunch.

`record-session-index.py` writes schema 3 self-bindings containing the validated
namespace, repository, physical work directory, worktree root and protected
roots. `mark-agent-registered.sh` compares the tool's project input with a
context re-resolved from the hook cwd; model-supplied or installed project keys
cannot choose a binding. A schema 2 self-binding remains identity/conflict
evidence only when its path-valued project matches the derived project, its
recorded cwd independently resolves to the same Git repository (or exact
non-Git workspace), and no strong owner record contradicts it. Missing,
logical, cross-repository or ambiguous legacy provenance is ignored until a
successful re-registration upgrades the session to schema 3.

The registration and reservation guards derive this lookup tuple from the
hook payload's physical cwd. A validated strong owner may preserve its custom
namespace, but ambient project variables cannot replace the workspace facts.
An invalid protected-file cwd is refused before Mail; release/invalidation
hooks skip mutations when that context cannot be resolved. A bare `AGENT_NAME`
does not bypass registration, while the `register_agent` MCP call remains
available so an unmanaged session can establish a binding.
