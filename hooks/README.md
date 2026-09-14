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
because a context marker exists. Validated parent/child ownership and consistent context propagation remain
responsibilities of the later launcher/registration migration. The workspace
context API below derives default protected roots without activating consumers.

The existing `agentstack_resolve_project_key`, `agentstack_resolve_protected_roots`,
`agentstack_installed_env_value` and their existing CLI commands keep their old
behavior in this slice. Top-level launchers now consume the workspace context API through
`bin/lib/agentstack-launch.sh`; see `docs/launchers.md`. Standalone bootstrap,
registration/session ownership, delegated children, watchers and Dashboard
still require their separate migrations. End-to-end isolation is not complete. Tests and this contract ship with the resolver rather than being deferred.

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
JSON, and never treats a context marker as delegated ownership proof. Other
consumers, installer diagnostics and generated instructions are not migrated
yet; see the explicit remaining boundaries in `docs/launchers.md`.

## Registration ownership primitives (non-activating prerequisite)

The shared registration library now exposes explicit invocation transport validation, token-backed local ownership, and folded-name claims. These definitions do not change existing registration or hook consumers in this prerequisite. Session/bootstrap activation and schema writer/reader migration remain one dependent change; an environment marker alone is not a new authorization mechanism.
