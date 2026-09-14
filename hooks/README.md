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
because a context marker exists. Validated parent/child ownership, protected
roots and consistent context propagation remain responsibilities of the later
launcher/registration migration.

The existing `agentstack_resolve_project_key`, `agentstack_resolve_protected_roots`,
`agentstack_installed_env_value` and their existing CLI commands keep their old
behavior in this slice. No current launcher or hook consumer calls the new
selector yet. Therefore this preparatory change does not by itself fix
cross-project registration, and existing launch/install instructions do not
change. Tests and this contract ship with the resolver rather than being deferred.

For a library call, source `hooks/project-context.sh` and call the function above.
For a read-only command-line check, run
`bash hooks/project-context.sh resolve-invocation-project-key /absolute/target`.
Pass an empty second argument before a non-Git fallback when there is no explicit
key. Neither form starts agents or alters a running installation.
