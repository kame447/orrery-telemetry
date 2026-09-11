# Provider 使用量 telemetry

Dashboard の `USAGE` strip は、各 agent の context remaining とは別に、Claude / Codex / Antigravity のアカウント単位の利用枠を表示する。

値は `GET /api/quotas` から取得し、既存の `GET /api/agents` には quota 取得処理を混ぜない。provider 側の CLI / App Server が失敗しても、他 provider と Dashboard 本体は継続する。

provider の refresh は provider ごとの lock と cache を持ち、cold miss 時は独立 provider を並列に取得する。一つの CLI timeout を他 provider の timeout に加算しない。

## 表示の意味

各 bucket は provider が実際に返した window だけを表示する。5h / 7d が存在すると仮定して補完しない。

`remaining_percent` は残量で、100 に近いほど余裕がある。取得に失敗した場合、直近の正常観測が短い stale window 内なら `stale`、それ以外は `unavailable` とする。

provider の stderr や例外本文はブラウザ向け API に返さず、Dashboard log にも任意本文を永続化しない。API の `reason` は安定した状態コード、log は provider 名と例外型までに限定する。

## Codex

Codex App Server の `account/rateLimits/read` を利用する。

`primary` / `secondary` の位置を 5h / 7d に固定対応させず、`windowDurationMins` から表示 label を決める。App Server は `/api/quotas` の cache miss 時だけ起動し、`/api/agents` の refresh では起動しない。

App Server の stdout 待ちは subprocess pipe を `selectors` へ直接登録せず、reader thread + queue + bounded deadline で処理する。このため POSIX と Windows で同じ transport path を利用できる。

`AGENTSTACK_CODEX_BIN` が設定されていれば、その executable を利用する。

## Antigravity

Antigravity CLI 1.1.11 以降で提供される read-only print command を利用する。1.1.10 以前では `/usage` を安全な非対話 command とみなさず、Dashboard から実行しない。

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

quota wrapper が注入された served Dashboard を `?demo=1` で開いた場合、USAGE strip は synthetic fixture のみを表示し、`/api/quotas` へ fall through して実アカウントの残量を取得しない。static demo bundle は現時点では wrapper 注入前の `index.html` を使うため、USAGE strip 自体を含まない。

## API

`GET /api/quotas` は常に provider 単位で状態を返す。一部 provider の取得失敗だけで endpoint 全体を 500 にしない。

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
