# Provider 使用量 telemetry

## PR #14 audit checkpoint

- Branch: `feat/provider-usage-telemetry`; base: `8db5dc11ccab08581ed44e9fc9e82a1001df8358`.
- Audit starting HEAD: `59f5aa9689d6e6002d91990120fc2cbe7a8ef299` (Issue #12).
- Owner: SnugBoltzmann; user-authorized continuation of PR #14, no merge.
- Baseline quota tests: 20 passed; audit regressions: 86 passed.
- Implemented: preserve real-page scripts, served-demo privacy guard/assets,
  mobile layout, provider validation/labels, observation expiry and nonblocking
  concurrent refresh, foreground startup, UTF-8 subprocess handling.
- Verified: Chromium 1440/390px, DECK/NETWORK, no JS errors, demo makes zero
  live API requests; wheel/sdist build and artifact contracts; live Codex read.
- Local full suite with inherited agent settings removed: 1691 passed,
  34 skipped, 1 installer-contract failure, subsequently corrected. The
  installer suite now passes all 15 tests, including real isolated installs.
  PR #13 owner approved reuse of its optional-provider test helper; only
  optional bin files are excluded, preserving checks on copied hooks/assets.
  The sample now includes the persisted Codex binary and quota directory.
- Next: publish the final corrections, rerun the full suite and final-head CI.
  The PR description records the terminal results and exact verified HEAD.
- Done: audit Issue #12 and complete PR diff, fix confirmed defects, re-audit,
  verify tests/build/UI and terminal CI on final PR HEAD; leave merge to user.

Dashboard の `USAGE` strip は、各 agent の context remaining とは別に、Claude / Codex / Antigravity のアカウント単位の利用枠を表示する。

値は `GET /api/quotas` から取得し、既存の `GET /api/agents` には quota 取得処理を混ぜない。provider 側の CLI / App Server が失敗しても、他 provider と Dashboard 本体は継続する。

provider の refresh は provider ごとの lock と cache を持ち、cold miss 時は独立 provider を並列に取得する。一つの CLI timeout を他 provider の timeout に加算しない。同一providerの取得中は後続pollを待機させず、期限内の前回値を`stale / refresh_in_progress`として返す。前回値がなければ`unavailable`を返す。

## 表示の意味

各 bucket は provider が実際に返した window だけを表示する。5h / 7d が存在すると仮定して補完しない。

上段には Claude / Codex の通常利用枠 / Antigravity を固定順のカードで表示する。
主要3 provider の領域は横スクロールせず、画面幅に応じて折り返す。
Spark など Codex の追加 limit は下段の追加情報へ分離する。通常枠が取得できない場合に
追加 limit を通常枠として表示しない。Codex の通常枠は `codex-<minutes>m` の ID で識別し、
未知の ID は追加情報へ表示する。Antigravity 内の Claude / GPT quota には
Antigravity 経由の枠であることを明記し、独立した Claude / Codex の利用枠と区別する。

各 quota の reset はブラウザのローカル時刻で `9/19 21:18 reset` の形式を常時表示する。
reset が取得できない場合も不明であることを表示し、推定日時を作らない。
追加情報に横スクロールが生じる場合は、左右の操作ボタンと edge fade で残りを示す。
スクロール位置や画面サイズの変更に合わせて、操作可能な方向を更新する。

`remaining_percent` は残量で、100 に近いほど余裕がある。取得に失敗した場合、直近の正常観測が短い stale window 内なら `stale`、それ以外は `unavailable` とする。

正常値をcacheから返す場合も観測から最大600秒（Claudeの設定が短ければその期限）で失効する。未来の観測時刻も受け付けない。reset時刻を過ぎた値は`stale / window_reset_pending`とし、次回の実観測なしに100%へ戻さない。`observed_at`はローカルで値を受け取った時刻であり、provider内部の測定時刻やアカウント識別情報ではない。

provider の stderr や例外本文はブラウザ向け API に返さず、Dashboard log にも任意本文を永続化しない。API の `reason` は安定した状態コード、log は provider 名と例外型までに限定する。

## Codex

Codex App Server の `account/rateLimits/read` を利用する。

`primary` / `secondary` の位置を 5h / 7d に固定対応させず、`windowDurationMins` から表示 label を決める。App Server は `/api/quotas` の cache miss 時だけ起動し、`/api/agents` の refresh では起動しない。

window durationとresetはJSON整数のみ受け入れ、真偽値・小数・文字列を暗黙変換しない。不正なdurationからbucketを作らず、不正なresetは未取得として扱う。

複数の利用枠がある場合は`limitName`または`limitId`を表示し、同じ長さのwindowも区別できるようにする。同じ利用枠のwindowを隣接させ、枠内では短いwindowから並べる。利用枠同士は最短の有効windowが短い枠から並べ、同じ長さならlimit IDで順序を固定する。`rateLimitsByLimitId`が返る場合は同じ枠の旧形式`rateLimits`より優先する。

App Server の stdout 待ちは subprocess pipe を `selectors` へ直接登録せず、reader thread + queue + bounded deadline で処理する。このため POSIX と Windows で同じ transport path を利用できる。

`AGENTSTACK_CODEX_BIN` が設定されていれば、その executable を利用する。

## Antigravity

Antigravity CLI **1.1.24以上**のread-only print commandを利用する。read-only `/usage`は1.1.11で導入されたが、headless呼び出しのstdout/stderrを子プロセスが保持して終了待ちが止まる問題は1.1.24で修正されたため、両方の修正を必要条件とする。[公式変更履歴](https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md)を根拠とし、古い版、非ゼロ終了、stderrだけのversion、pre-release、別CLIのbannerではusageを実行しない。

```sh
agy -p "/usage" --output-format json
```

ただし Antigravity CLI は有効な認証がない場合に Google Sign-In を開始し得る。常駐 Dashboard の telemetry poll が認証 UI を勝手に起動しないよう、Antigravity quota は明示 opt-in とする。

インストール済みの常駐 Dashboard では、runtime marker を作るのが最も単純な opt-in になる。marker は次回の provider cache refresh 時に確認されるためサービス再インストールは不要だが、既存 cache がある場合は最大 180 秒程度反映が遅れる。

```sh
mkdir -p ~/.agentstack/runtime
touch ~/.agentstack/runtime/antigravity-quota.enabled
```

無効化する場合は marker を削除する。

```sh
rm ~/.agentstack/runtime/antigravity-quota.enabled
```

手動起動など、Dashboard プロセスへ環境変数を確実に渡せる場合は `AGENTSTACK_ANTIGRAVITY_QUOTA_ENABLED=1` でも opt-in できる。marker の場所は `AGENTSTACK_RUNTIME_DIR` に追従し、必要なら `AGENTSTACK_ANTIGRAVITY_QUOTA_OPT_IN_FILE` で明示できる。

opt-in が有効でない間は `agy --version` も `/usage` も実行せず、Antigravity は `unavailable / telemetry_opt_in_required` として表示する。opt-in は「この常駐 telemetry から Antigravity CLI を呼び出してよい」という明示的な許可として扱う。

CLI の envelope や field casing が release 間で異なっても、返却された quota group / bucket のみを正規化する。`displayName` / `bucketId` / `remainingFraction` と、その互換表現である snake_case / nested `remaining` の両方を受け付けるが、Gemini / Claude / GPT などの固定 bucket を ORRERY 側では作らない。

fractionは0〜1だけを受け付け、1を超えた値をpercentageに読み替えない。disabledまたは無効なfractionのbucketは表示しない。windowは明示フィールドを優先し、model名の途中の数字をdurationとして誤認しない。

binary は既存 provider と同じ `AGENTSTACK_GEMINI_BIN` を優先する。未設定時は PATH に加えて `~/.local/bin/agy`、Homebrew の代表的な場所も確認する。

## Claude

Claude Code は status line 入力に subscription rate limit を含める。ORRERY はユーザー所有の `statusLine` command を自動で上書きしないため、Claude quota の観測は明示的な opt-in とする。

同梱の observer は install 後、次の場所にある。

```text
~/.agentstack/dashboard/claude_quota_observe.py
```

Claude の `statusLine.command` としてこの script を Python で実行すると、status line の stdin JSON から `rate_limits` だけを `~/.agentstack/runtime/claude-quota.json` へ保存し、Dashboard がそれを読む。複数 Claude session が同時に status line を更新しても壊れないよう、同一 directory の unique temporary file から atomic replace し、snapshot は可能な環境で mode 600 にする。

例:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.agentstack/dashboard/claude_quota_observe.py"
  }
}
```

既に独自 status line を使用している場合、この例で置き換えないこと。既存 command と observer の出力を両立させる wrapper が必要になる。ORRERY installer は現時点ではそこを自動変更しない。

Claude quota がまだ観測されていない場合、Dashboard は推定値を作らず `unavailable` を表示する。

## Demo mode

quota wrapper が注入された served Dashboard を `?demo=1` で開いた場合、USAGE stripはsynthetic fixtureのみを表示する。wrapperは既存のdemo engineとstoryも配信し、全ページscriptより前にAPI通信の遮断を設定する。demo assetが欠けた場合も実APIへfallbackしない。static demo bundleは現時点ではwrapper注入前の`index.html`を使うため、USAGE strip自体を含まない。

## API

`GET /api/quotas` は常に provider 単位で状態を返す。一部 provider の取得失敗だけで endpoint 全体を 500 にしない。

取得はread-onlyだがcache miss時にはCLIを起動するため、cross-origin browser requestは取得前に403で拒否する。認証情報は既存CLI側に委ね、Dashboardからlogin/logoutや設定の書き換えは要求しない。Dashboard自体は既存のlocalhost/trusted LAN境界を使い、独自login layerは追加しない。

常駐起動と`agentctl.sh fg`はともにquota wrapperを使う。明示的に旧`server.py`を起動した場合はquota追加前のAPI/UIとなる。

API契約の参照: [Codex App Server](https://learn.chatgpt.com/docs/app-server)、[Claude status line](https://code.claude.com/docs/en/statusline)。実アカウントの値や認証ファイルをfixtureに含めない。

概念的な response:

```json
{
  "ts": 1789123456,
  "degraded": true,
  "providers": [
    {
      "provider": "codex",
      "status": "ok",
      "source": "codex-app-server",
      "observed_at": 1789123440,
      "buckets": [
        {
          "id": "codex-300m",
          "label": "5h",
          "scope": "account",
          "used_percent": 23.5,
          "remaining_percent": 76.5,
          "window_seconds": 18000,
          "resets_at": 1789130000,
          "quality": "exact"
        }
      ]
    }
  ]
}
```

`quality` は `exact` / `derived` / `estimated` のいずれかで、根拠のない confidence score は持たない。
