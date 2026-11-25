# Google Antigravity / Gemini provider

ORRERY では、Claude Code や Codex と並んで Google Antigravity CLI（`agy`）を
使えます。利用者が Antigravity でサインインしたアカウントを使います。

## Projectless fresh runtime（fork）

fork の fresh runtime は `program=antigravity` を正規の本人登録、親子関係、絶対pathの予約、起動証跡へ接続します。通常の旧runtimeは従来の経路を使います。fresh の NEW AGENT では親あり・親なしのどちらも利用でき、編集対象の resources を明示します。fresh 自体の worktree 自動生成は未対応のため、作業folderは先に用意します。全体の移行条件は [fresh移行手順](fresh-global-update.md) に従います。

fresh 起動は公式 `agy --input-format stream-json --output-format stream-json` を使用し、同じ会話を維持して Mail から追加指示を受け取ります。TTYから起動した場合は行単位の入力と `/exit` も使えます。これはネイティブの全画面TUIとは異なる操作画面です。再開は保存された本人・native conversation IDを照合し、元の依頼を自動で繰り返しません。Cockpitのnative transcript readerは今回の対象に含みません。

起動専用の小さなfolderを `--add-dir` で渡し、その `.agents/hooks.json` と `.agents/mcp_config.json` から予約guardと一意名のfresh Mail接続を読み込みます。利用者のHOME、ログイン、permissions、作業folderの既存設定は変更しません。`--settings` にhooksやMCP定義を入れて読み込ませる方式ではありません。owner資格値をprompt、argv、MCP設定へ入れず、旧project key／旧Mail資格へfallbackしません。

最初のturnは初期化確認だけです。実CLIのinitと実hookが返したconversation ID、本人、instance、現在のlaunchが一致し、非空の正常応答が得られてから依頼内容を送ります。新規の会話では SessionStart、同じ会話の再開では現在の起動の PreInvocation を確認します。再開時に過去の作業領域のフックも読み込まれる場合は、その出自・同一会話・終了を検査し、新しい起動のフックだけが初期化の証拠を作ります。hookが読み込まれなければ依頼を渡さず停止します。予約guardはagyの正式な `decision=deny` で拒否し、native hook timeoutより短いdeadlineで失敗を返します。`write_file`／`edit_file` は `Path`、従来の `write_to_file`／`replace_file_content`／`multi_replace_file_content` は `TargetFile` を検査します。対象キーが欠ける場合や両方のキーがある場合は拒否し、別のキーへ読み替えません。guard設定は起動ごとに保存するため、更新後は新規起動または明示的な再開で読み込み直します。対象パスの契約が未確認の `notebook_edit` と `sed_file` は明示拒否します。ネイティブ権限は広げません。必要なMCP操作をheadlessで承認できない場合も、拒否を成功として扱いません。

Mail通知は保護されたprivate socketで待機中のrunnerを起こし、本人のpendingを正規APIから取得して次のstream turnへ渡します。送信、通知引渡し、既読、ACK、モデルの仕事完了は別の事実です。通知の引渡しだけで既読やACKを付けません。busy中に受けたwakeは現在のturnが終わってから再確認します。

fresh の native CLI はrunnerと独立した専用supervisorが直接の親として保持します。起動時はprivate gateを閉じたままPID・起動時刻・UID・process groupを照合し、起動証跡の保存に成功してからCLIをexecします。runnerとのprivate lifetime channelが閉じるとsupervisorは終了処理を行います。観測不能なら未回収の直接の子を保持して再確認し、無関係なgroupを推測で停止しません。runnerの待機がtimeoutしてもsupervisorを強制終了しないため、持続する観測不能は運用上の調査が必要です。supervisor自体が強制終了した場合まで自動復旧を保証するものではありません。

終了時はこの起動が作ったnative process groupの消滅を確認してから、この子に宣言した予約だけを解放します。終了を確認できない場合は予約を保持し、通常の `resolve-ended` 経路で明示確認します。別のprocess、既存サービス、旧Mailは停止しません。実CLI・MCP・追加指示・編集guard・終了を確認した範囲と、模擬providerによる回帰試験を区別して受け入れます。

## Legacy provider prerequisites

先に ORRERY core と稼働中の ORRERY Mail を用意します。
[Google の手順](https://antigravity.google/docs/getting-started?tab=cli)で CLI を導入し、
利用者がサインインします。macOS / Linux 向けの公式 `install.sh` は最後に
`agy install` を呼び、シェルの PATH 設定などを変更します。利用者の了承を得て実行し、
設定ファイルをそのままにしたい場合はバイナリだけを導入する段階で止めます。
現在のシェルから次の両方が通ることを確認します。

```sh
agy --version
agy models
```

標準のバイナリ配置は `~/.local/bin/agy` です。

## Install the optional provider payload

ORRERY Telemetry の checkout で、まず導入先とコピー予定を確認します。

```sh
scripts/install-gemini-provider.sh --dry-run
```

導入方法は使う経路で選びます。`--dry-run` は `agy` のない環境でも使えます。

- Dashboard の **NEW AGENT** または委任する子だけで使う場合は
  `scripts/install-gemini-provider.sh` を実行します。子の Mail MCP は作業用 worktree に用意されます。
- **`agent-start-gemini` で立てる親なし agent から Mail を送る場合**は、
  `scripts/install-gemini-provider.sh --dry-run --configure-mcp` で追加先を確認します。
  `~/.gemini/config/mcp_config.json` に `orrery-mail` を設定する変更を利用者に示し、
  了承後に次を実行します。すでに provider を導入済みの場合も同じコマンドです。

```sh
scripts/install-gemini-provider.sh --configure-mcp
```

導入後、稼働中のエージェントの作業終了と予約の解放を確認してから Dashboard を再起動します。

```sh
~/.agentstack/dashboard/agentctl.sh restart
```

optional installer は Gemini 専用のファイルだけをコピーし、dashboard/server.py は置換しません。必要な core が古い場合は
core を更新してから再実行します。

## Usage left に利用枠を表示

利用枠を表示する場合は、利用者の了承を得て Dashboard の runtime ディレクトリに
`antigravity-quota.enabled` を作ります。標準の導入先なら次の手順です。
`AGENTSTACK_RUNTIME_DIR` をカスタマイズした場合は、Dashboard サービスが使う設定値を指定します。

```sh
quota_runtime="$HOME/.agentstack/runtime"
test -d "$quota_runtime" && touch "$quota_runtime/antigravity-quota.enabled"
```

有効にすると Dashboard は `agy -p /usage --output-format json` でサインイン済みの
アカウントの利用枠を読みます。CLI 1.1.24 以降が必要です。導入後は Cockpit の
**Usage left** で **Antigravity (Gemini)** を確認します。初回の取得や、すでに表示されている
`unavailable` の更新には、キャッシュ分の最大約3分かかることがあります。
`unavailable / no windows` が続く場合は、marker の場所、CLI のバージョン、サインインを確認し、
`agy -p /usage --output-format json` が利用枠を返すか確認します。利用枠の表示には Gemini API キーは不要です。

既存のGemini環境を更新する場合は、coreの更新後、同じ検証済みrevisionから
この追加インストーラーも再実行し、作業中のエージェントが終了してから
Dashboardを再起動してください。coreだけの再インストールは既存providerを
意図的に保持するため、Gemini部分の更新にはなりません。モデルID追加のために
古いruntimeを上書きせず、追加インストーラーでruntimeと起動スクリプトを
一緒に更新してください。

## Top-level launcher

```sh
~/.agentstack/bin/agentstack-gemini-setup --check
~/.agentstack/bin/agent-start-gemini /path/to/project
```

`--check` は管理されたグローバル MCP 定義を確認します。起動後は `agy` の `/mcp` で
`orrery-mail` が接続済みであることを確認し、Mail にはその `send_message` を使います。
プロジェクト専用の MCP 定義を使う場合も、接続の確認場所は `/mcp` です。
名簿への登録は別の処理です。MCP が見つからない、または承認が必要なら利用者に伝えます。
この対話起動は legacy runtime 用です。global runtime の
`GEMINI_INTERACTIVE_RUNTIME_UNSUPPORTED` が出る場合は、対応する委任経路を使います。

Defaults:

- binary: `agy`
- model: `gemini-3.8-flash-high`
- effort: `high` (from `AGENTSTACK_GEMINI_EFFORT`)

The CLI launchers (`agent-start-gemini`, `spawn_gemini_child.sh`, and a direct
`spawn_gemini_preregistered.sh` run) keep this `high` fallback. The Dashboard
does not use it; see [Dashboard NEW AGENT](#dashboard-new-agent).

For binary-free validation:

```sh
AGENTSTACK_GEMINI_BIN=agy-not-installed \
  ~/.agentstack/bin/agent-start-gemini --dry-run /path/to/project
```

Dry-run stops before binary lookup, tmux, ORRERY Mail registration, and MCP
bootstrap. A real launch still fails clearly if `agy` is absent.

## Delegated child

```sh
PARENT_AGENT=<parent-name> \
~/.agentstack/hooks/spawn_gemini_child.sh \
  --resources "src/**,tests/**" \
  "Implement the requested change and run relevant tests." \
  /path/to/project
```

The launcher pre-registers the child, creates an isolated git worktree, reserves
declared resources, sends the task to Antigravity over stream-json stdin, and
uses the session-bound MCP wrapper. Owner-token values are not placed in the
task, argv, or Antigravity MCP configuration.

A nominal Antigravity `SUCCESS` is not enough when required actions were denied
or the textual response is empty. Those cases are reported to the parent as
incomplete. A non-zero launcher/runtime status is likewise incomplete.

## Dashboard NEW AGENT

With the optional provider payload installed and the Dashboard restarted,
`NEW AGENT` shows an `Antigravity` engine tab. A launch reuses the delegated
child path above through `spawn_gemini_preregistered.sh`.

![NEW AGENT with the Antigravity tab selected: the resources row sits under the task, and the footer explains what is still required](images/new_agent_capabilities_dark.png)

![The same state in the light theme](images/new_agent_capabilities_light.png)

Requirements checked before ORRERY Mail registration, so a launch that cannot
start does not leave a retained child identity behind:

- a parent agent: standalone Antigravity launches are rejected, and the parent
  must have its local runtime owner token;
- the launch directory must be inside a git repository, and the worktree base
  (default `HEAD`) must resolve to a commit;
- `agy`, `tmux`, the selected Python, `git`, the adapter hook, and the Gemini
  provider helpers under `~/.agentstack/bin` must be present;
- `AGENTSTACK_GEMINI_MODELS` を明示した場合、モデルはその許可リストに限定されます。
  未設定なら、同梱の `gemini-3.8-flash-{high,medium,low}` と `agy models` が返した有効な
  Gemini ID を表示します。モデルの取得は起動前の検査と同じ方法で `agy` を探すため、
  Dashboard の PATH に `~/.local/bin` がなくても標準の場所を使えます。
  `gemini-3.8-flash` しか表示されない場合は、`agy models` の結果、Dashboard を再起動したか、
  許可リストの設定を確認します。
  取得を待つ時間は5秒、出力は64 KiBまでです。成功は5分間キャッシュされ、失敗時は前回の
  成功結果を保ち、5秒後以降に再試行します。CLI が返した effort 付き Claude・GPT-OSS ID は、ネイティブの ID と衝突しない場合に限り利用できます。
  許可リストに Claude／Codex と同じ ID を指定すると Antigravity のタブは無効になります。
  リストにない正式な Gemini ID も起動時に指定でき、利用可否は CLI が確認します。

The model card displays each Gemini family once (for example, `gemini-3.8-flash`,
`gemini-4-argon`, and `gemini-4-hydrogen`). The original `gemini-3.8-flash-high` remains the default;
only efforts present in the current model list are offered for each family.
Before registration, Dashboard resolves the selected effort to the corresponding CLI model:
`low` → `gemini-3.8-flash-low`, `medium` → `gemini-3.8-flash-medium`, and
`high` → `gemini-3.8-flash-high`. Registration, launch logs, and `agy --model`
use that same resolved id alongside the explicit `--effort`. A custom model
allow-list must include the matching variant; otherwise the request is rejected
before registration. Without a custom allow-list, a formal `gemini-*` ID is
validated by the CLI instead of being refused simply because discovery omitted it.
Model IDs without an effort suffix are passed unchanged. Spawn and `dry_run`
do not run `agy models` for native Gemini IDs; cross-provider IDs require CLI catalog validation at launch.

モデルの自動検出は、Dashboardの起動処理と同じ探索経路を使います。
サービスのPATHから省かれている場合も `~/.local/bin` を探します。
`AGENTSTACK_GEMINI_BIN` の明示指定は引き続き優先し、そのコマンドが
見つからなくても別のCLIへ勝手に切り替えません。Dashboardと3種類の
Gemini起動スクリプトは `xhigh` に対応します。ただし画面では、検出した
一覧または明示許可リストにそのvariantがあるモデル系列にだけ提示します。
Hydrogenが追加されてもArgonやFlashへ `xhigh` を一律に提示しません。
新しいモデルIDを固定一覧へ追記する必要はなく、既定モデルの自動変更や
明示許可リストの拡大も行いません。

Engine and isolation rules:

- **Effort is explicit.** The Dashboard selects no effort for Antigravity, keeps
  `SPAWN` disabled until `low`, `medium`, `high`, or a supported `xhigh` is chosen, and the server
  rejects a request without one (`effort required for provider gemini`). The
  CLI launchers' `high` fallback is not applied to Dashboard launches.
- **Isolated worktree is forced.** The isolation checkbox is locked on while
  Antigravity is selected; switching to Claude or Codex restores the previous
  choice, and hidden resource declarations are not sent to those providers.
- **Resources are required.** Enter comma-separated paths relative to the
  repository root, for example `src/**,tests/**`. Absolute paths, `..`, `~`,
  control characters, a leading `-`, backslashes, and `.git` are rejected, as
  is any existing symlink component that resolves outside the repository. The
  adapter repeats the check inside the new worktree before reserving anything.
  Declarations are normalized (whitespace, `./`, duplicate slashes, repeats)
  before they are reserved, and a leading `-` is rejected in the normalized
  form too (`./-rf`). Wildcards are matched the way a case-insensitive
  filesystem such as default APFS resolves names, so `.GI?/config` or `SR?/**`
  is judged against `.git` or `src` in any letter case.

The launcher delivers the full task to Antigravity and reports the result to
the parent automatically, so the Dashboard sends no separate task mail for an
Antigravity child. Launch status, `/api/spawn-status`, and
`dashboard/logs/spawn.log` record `provider=gemini`.

The core Dashboard modal interprets the provider `capabilities` contract. The
optional payload only adds the Antigravity catalog item and its `/api/spawn`
branch; it does not rewrite the page while serving it. If the core page lacks
the capability marker or resources control, the Antigravity tab is omitted and
Antigravity spawn requests fail closed; the reason is written to the Dashboard
log. Claude and Codex are unaffected, including when the extension itself
cannot be loaded.

## Dashboard controls

The Dashboard uses the same provider-aware process-tree measurement for Claude,
Codex, and Antigravity. For a delegated Antigravity child, `EXIT` re-reads the
process tree immediately, sends `SIGINT` only to the measured `agy` process, and
leaves the launcher-owned shell runner alive so report/release/retire/cleanup can
finish. For a top-level interactive Antigravity session, `EXIT` keeps the normal
interactive `/exit` path.

The legacy runtime does not implement Antigravity conversation resume or native
transcript history. Gone or retired Antigravity entries therefore fail closed
instead of falling through to Claude/Codex transcript lookup. A top-level
Antigravity session whose `agy` process has exited but whose human shell remains
is kept attachable as `finished`; Dashboard jump does not kill that shell to
attempt an unsupported resume.

## Headless permission caveat

`agent-start-gemini` と Gemini child の launcher は `--mode` を指定しません。
CLI の `/config` にある実行モードと `toolPermission` が適用されます。1.5.7 以降、
`agentMode` が未設定なら、利用可能な環境で Auto、それ以外は `default` になります。
`accept-edits` は作業領域の編集を自動承認し、`plan` は調査と計画に使います。
Auto が利用できるかはアカウントと端末の sandbox に依存し、MCP の呼び出しには別途確認が出ることがあります。
[Google の CLI 権限ガイド](https://antigravity.google/docs/permissions?tab=cli)

Gemini child は headless で動き、対話で答える必要がある承認は失敗として返ります。
[Claude／Codex の子の起動設定](delegation.md)とは別に扱われます。たとえば親付き Codex の
`--ask-for-approval never` は Gemini に引き継がれません。ORRERY は Gemini に
`--sandbox` や `--dangerously-skip-permissions` を追加せず、CLI の設定に従います。

In a measured macOS run using Antigravity CLI 1.1.27, headless `view_file`
inside the delegated worktree was auto-denied until `read_file` was explicitly
allowed by the user. The stderr message stated that headless mode could not
prompt for the required permission.

Use the narrowest rule that covers the **resolved** delegated-worktree root.
New installs default to `~/.agentstack/worktrees`; replace the example user
home with the actual resolved path. Keep the legacy `/private/tmp` rule only
while a pre-#57 worktree remains there:

```json
{
  "permissions": {
    "allow": [
      "read_file(/path/to/home/.agentstack/worktrees)",
      "read_file(/private/tmp/cc-worktrees)"
    ]
  }
}
```

Merge such a rule with the user's existing settings; do not replace existing
permission entries. If `AGENTSTACK_WORKTREE_ROOT` is customized, scope the rule
to that resolved root instead. Remove the legacy rule after its old worktrees
are gone; ORRERY does not migrate or delete them.

ORRERY does not add `read_file(*)`, does not enable
`--dangerously-skip-permissions`, and does not otherwise broaden user-owned
Antigravity permissions.

## Credential boundary

The session-bound wrapper resolves the current agent's mode-0600 runtime token
file when the MCP process starts. Antigravity workspace/global MCP config stores
only the wrapper command, not the owner-token value or token-file path.

`agy` and ORRERY still run as the same operating-system user. This reduces
accidental configuration/argv exposure; it is not an OS-level isolation
boundary against another process running as that user.

## Design boundary

The provider adds `program=antigravity` / `agy` to the core's shared
process-tree liveness. The provider extension ships with the optional installer.
The core installer leaves an already-installed extension in place.

- Top-level `agy` exits to its interactive shell, which remains attachable as `finished`.
- A delegated launcher reports the result, releases reservations, soft-retires the identity,
  and removes transient config and credentials before its own exit.

The Dashboard reports measured process and tmux state.

## Measured macOS E2E

The pre-upstream prototype was exercised on macOS with Antigravity CLI 1.1.27
and Gemini 3.8 Flash High.

Normal delegated completion was measured through preregistration, worktree
creation, reservation, headless execution, non-empty `SUCCESS`, parent
`Gemini child complete` reporting, release/retire/cleanup, and child tmux
session disappearance.

A live Dashboard `EXIT` run produced Antigravity `ERROR / interrupted`; the
launcher-owned runner remained alive long enough to report
`Gemini child incomplete`, release the reservation, retire/clean transient
state, and then let the child tmux session disappear. The parent and
`mail-watcher` survived, and the same resource was immediately reservable by a
subsequent child.

Those real-machine observations are evidence for the provider lifecycle. Final
upstream-ready validation must be repeated after integrating onto the current
upstream base.
