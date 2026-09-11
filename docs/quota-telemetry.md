# Provider 使用量 telemetry

Dashboard の `USAGE` strip は、各 agent の context remaining とは別に、Claude / Codex / Antigravity のアカウント単位の利用枠を表示する。

値は `GET /api/quotas` から取得し、既存の `GET /api/agents` には quota 取得処理を混ぜない。provider 側の CLI / App Server が失敗しても、他 provider と Dashboard 本体は継続する。

## 表示の意味

各 bucket は provider が実際に返した window だけを表示する。5h / 7d が存在すると仮定して補完しない。

`remaining_percent` は残量で、100 に近いほど余裕がある。取得に失敗した場合、直近の正常観測が短い stale window 内なら `stale`、それ以外は `unavailable` とする。

## Codex

Codex App Server の `account/rateLimits/read` を利用する。

`primary` / `secondary` の位置を 5h / 7d に固定対応させず、`windowDurationMins` から表示 label を決める。App Server は `/api/quotas` の cache miss 時だけ起動し、`/api/agents` の refresh では起動しない。

`AGENTSTACK_CODEX_BIN` が設定されていれば、その executable を利用する。

## Antigravity

Antigravity CLI 1.1.12 以降で提供される read-only print command を利用する。1.1.11 以前では `/usage` を安全な非対話 command とみなさず、Dashboard から実行しない。

```sh
agy -p "/usage" --output-format json
```

CLI の envelope や field casing が release 間で異なっても、返却された quota group / bucket のみを正規化する。`displayName` / `bucketId` / `remaining.remainingFraction` と、従来の snake_case 相当の両方を受け付けるが、Gemini / Claude / GPT などの固定 bucket を ORRERY 側では作らない。

binary は既存 provider と同じ `AGENTSTACK_GEMINI_BIN` を優先する。未設定時は PATH に加えて `~/.local/bin/agy`、Homebrew の代表的な場所も確認する。

## Claude

Claude Code は status line 入力に subscription rate limit を含める。ORRERY はユーザー所有の `statusLine` command を自動で上書きしないため、Claude quota の観測は明示的な opt-in とする。

同梱の observer は install 後、次の場所にある。

```text
~/.agentstack/dashboard/claude_quota_observe.py
```

Claude の `statusLine.command` としてこの script を Python で実行すると、status line の stdin JSON から `rate_limits` だけを `~/.agentstack/runtime/claude-quota.json` へ保存し、Dashboard がそれを読む。

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
