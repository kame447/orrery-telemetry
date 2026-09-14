from pathlib import Path
root = Path.cwd()
for name in ('agent-start','agent-start-codex','agent-start-gemini'):
 p=root/'bin'/name
 s=p.read_text()
 s=s.replace('ags_load_env\n', 'ags_load_env\nags_parse_top_level_args "$@"\n', 1)
 s=s.replace('DIR="$(ags_choose_dir "$@")"', 'DIR="$(ags_choose_dir ${AGS_LAUNCH_DIR_ARGS[@]+"${AGS_LAUNCH_DIR_ARGS[@]}"})"',1)
 s=s.replace('[[ -n "$DIR" ]] || exit 0\n', '[[ -n "$DIR" ]] || exit 0\nags_prepare_top_level_context "$DIR" "$AGS_EXPLICIT_PROJECT_KEY" \\\n  || ags_die "cannot resolve launch project context"\nDIR="$AGENTSTACK_PROJECT_WORK_DIR"\n',1)
 if name=='agent-start':
  s=s.replace('PROJECT_KEY="${AGENTSTACK_PROJECT_KEY:-}"\n','',1)
 if name=='agent-start-gemini':
  old='DRY_RUN=false\n\nif [[ "${1:-}" == "--dry-run" ]]; then\n  DRY_RUN=true\n  shift\nfi'
  assert old in s
  s=s.replace(old,'DRY_RUN="$AGS_LAUNCH_DRY_RUN"',1)
  s=s.replace("  printf '%s\\n' \"$(build_gemini_cmd)\"", "  printf '%s: project=%s repository=%s roots=%s\\n' \\\n    \"$AGS_PROG\" \"$PROJECT_KEY\" \"$AGENTSTACK_PROJECT_REPOSITORY\" \"$AGENTSTACK_PROTECTED_ROOTS\"\n  printf '%s\\n' \"$(build_gemini_cmd)\"",1)
 # The options are command arguments, not data interpolated into the pane code.
 inner='INNER="$(mktemp -t '
 idx=s.index(inner)
 s=s[:idx]+'TMUX_PROJECT_OPTIONS="$(ags_tmux_project_options)"\nags_clear_client_project_context\n'+s[idx:]
 line='-c $(printf \'%q\' "$DIR") \\\n'
 assert line in s
 s=s.replace(line,line+'  $TMUX_PROJECT_OPTIONS \\\n',1)
 # Also sanitize selectors inherited from an existing server in the pane itself.
 if name=='agent-start':
  s=s.replace('  "if [ -n $(printf', '  "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR AGENTSTACK_PROJECT_CONTEXT AGENTSTACK_LOOKUP_PROJECT_KEY; if [ -n $(printf',1)
 else:
  s=s.replace('  "source $(printf', '  "unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR AGENTSTACK_PROJECT_CONTEXT AGENTSTACK_LOOKUP_PROJECT_KEY; source $(printf',1)
 # Document the common override at each entry point.
 s=s.replace('# Env:\n', '# Use --project-key KEY for an explicit top-level namespace override.\n#\n# Env:\n',1)
 p.write_text(s)

def replace_once(path: str, before: str, after: str) -> None:
    file = root / path
    text = file.read_text(encoding='utf-8')
    if text.count(before) != 1:
        raise RuntimeError(f'{path}: expected exactly one replacement anchor')
    file.write_text(text.replace(before, after, 1), encoding='utf-8')

ja = '''## Top-level project context

`agent-start`、`agent-start-codex`、任意導入の `agent-start-gemini` は、directory の選択後、登録や tmux session 作成より前に、その起動先から project context を解決します。親 shell、installed `env.sh`、既存 tmux server に残った project key や protected roots は、この選択の正本ではありません。

明示的に namespace を指定する場合は `agent-start-codex --project-key KEY DIR` のように指定します。3種類の launcher が同じ引数を受け付けます。Git では linked worktree と main checkout が repository identity を共有し、独立 clone は分離されます。non-Git directory は物理パスが既定の key です。従来の non-Git namespace を継続するときも `--project-key` で明示してください。

`work_dir` は指定した subdirectory を維持し、protected roots はその worktree 全体を保護します。explicit key は namespace であり、保護対象のパスではありません。Git metadata や別の main checkout を追加の保護 root として自動採用しません。Git 調査失敗、壊れた metadata、bare repository、既存のコロン区切り形式で表せない root は、別 project への fallback で隠さず起動前に拒否します。

新しい tmux session には解決済みの値を `new-session -e` で明示し、新規 server の global environment には今回の project を埋め込みません。既存 pane 内では CLI と bootstrap の process environment を更新しますが、他 pane に影響する session-wide project metadata の移行はまだ行いません。`AGENTSTACK_PROJECT_CONTEXT=1` を ownership の証拠にはしません。予約済み child の context を top-level の override として渡してはいけません。

この段階で移行するのは top-level launcher の入口と引き渡しまでです。standalone bootstrap、registration/session index、delegated child、watcher、Dashboard の project ownership は後続の独立修正です。installed AGENTS.md が StudyPlanner 等の固定 key を指示する既存問題も残っているため、この段階だけで multi-project の end-to-end isolation が完成したとは扱いません。repository の instruction renderer 修正と、ユーザーの既存設定の再生成・再インストールは別作業です。

Gemini の `--dry-run` は context と予定コマンドだけを表示し、tmux・Mail・CLI を起動しません。model/effort、Codex の sandbox/approval と OAuth、Gemini が REPL 終了後に shell を残す動作は維持します。

'''
en = '''## Top-level project context

`agent-start`, `agent-start-codex`, and the optional `agent-start-gemini` resolve the selected directory before registration or tmux session creation. Project keys and protected roots left in the parent shell, installed `env.sh`, or an existing tmux server are not authoritative inputs to that selection.

Use `agent-start-codex --project-key KEY DIR` for an explicit namespace; all three launchers accept the same option. Main and linked worktrees share repository identity, while independent clones remain separate. A non-Git directory defaults to its physical path. Continuing a previous non-Git namespace also requires an explicit `--project-key`.

`work_dir` preserves a selected subdirectory, while protected roots cover its whole worktree. An explicit key is a namespace, not a protected pathname. Git metadata and another main checkout are not added as protection roots automatically. Git inspection failures, broken metadata, bare repositories, and roots the legacy colon-delimited format cannot represent are rejected before launch, not hidden by a fallback to another project.

A fresh tmux session receives the resolved values through `new-session -e`, without seeding a new server's global environment with this project. Inside an existing pane, the CLI/bootstrap process environment is updated; migrating session-wide project metadata that could affect other panes is deferred. `AGENTSTACK_PROJECT_CONTEXT=1` is not ownership proof. Never forward a reserved child's context as a top-level override.

This slice migrates only top-level entry and handoff. Standalone bootstrap, registration/session indexes, delegated children, watchers, and Dashboard project ownership remain separate follow-up work. Installed AGENTS.md instructions that name a fixed project such as StudyPlanner are still an open problem, so this slice is not end-to-end multi-project isolation. Repository instruction-renderer changes and regeneration/reinstallation of a user's existing settings are separate operations.

Gemini `--dry-run` prints the context and planned command without starting tmux, Mail, or the CLI. Model/effort settings, Codex sandbox/approval and OAuth behavior, and Gemini's post-REPL shell lifecycle are unchanged.

'''
for path, content in (('docs/launchers.md', ja), ('docs/launchers.en.md', en)):
    replace_once(path, '## tmux session\n', content + '## tmux session\n')
replace_once('docs/launchers.md',
    '`AGENTSTACK_PROJECT_KEY` が未設定、または ORRERY Mail が到達不能でも CLI 自体は preselected name で起動します。ただし mail、reservation、project-scoped dashboard 機能は使えません。',
    'top-level launcher は project context を先に確定します。ORRERY Mail が到達不能な場合は従来どおり preselected name で CLI を起動しますが、coordination は利用できません。直接呼び出す standalone bootstrap の legacy な key 解決は、この段階では変更しません。')
replace_once('docs/launchers.en.md',
    'If `AGENTSTACK_PROJECT_KEY` is unset or ORRERY Mail is unreachable, the CLI itself still starts with the preselected name. Mail, reservations, and project-scoped dashboard features are unavailable, however.',
    'Top-level launchers first establish project context. When ORRERY Mail is unreachable, the CLI still starts with the preselected name as before, but coordination is unavailable. Legacy key selection in a directly invoked standalone bootstrap is unchanged in this slice.')
replace_once('hooks/README.md',
    'No current launcher or hook consumer calls the new\nselector yet. Therefore this preparatory change does not by itself fix\ncross-project registration, and existing launch/install instructions do not\nchange.',
    'Top-level launchers now consume the workspace context API through\n`bin/lib/agentstack-launch.sh`; see `docs/launchers.md`. Standalone bootstrap,\nregistration/session ownership, delegated children, watchers and Dashboard\nstill require their separate migrations. End-to-end isolation is not complete.')
replace_once('hooks/README.md',
    'This layer changes only the shared library, its focused tests and this API\ncontract. Top-level launchers, bootstrap, hooks, watcher, Dashboard, installer\nand generated AGENTS.md instructions are not migrated here. User-facing install\nand launch documentation remains unchanged because those paths still use their\nlegacy behavior; cross-project registration is not fixed until they migrate.',
    'The context layer itself remains read-only. Its top-level launcher adapter\nvalidates representability before exporting a complete tuple, does not eval\nJSON, and never treats a context marker as delegated ownership proof. Other\nconsumers, installer diagnostics and generated instructions are not migrated\nyet; see the explicit remaining boundaries in `docs/launchers.md`.')
# Keep the existing identity tests' scope: use real context helpers and only
# replace the external tmux/Mail boundary as before. Do not weaken assertions.
replace_once('tests/test_identity_isolation.py',
    '    libdir.mkdir(parents=True)\n    launcher = bindir / "agent-start"',
    '    libdir.mkdir(parents=True)\n    (tmpdir / "hooks").mkdir()\n    (tmpdir / "hooks/project-context.sh").write_text(_read("hooks/project-context.sh"), encoding="utf-8")\n    launcher = bindir / "agent-start"')
replace_once('tests/test_identity_isolation.py',
    '    (libdir / "agentstack-launch.sh").write_text(\n',
    '    (libdir / "agentstack-launch.sh").write_text(\n        f\'source "{_ROOT / "bin/lib/agentstack-launch.sh"}"\\n\'\n')
