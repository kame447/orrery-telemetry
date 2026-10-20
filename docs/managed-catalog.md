# Native catalog and effort capabilities

## 現在の組合せ

[provider・model・harness の説明](getting-started.md#provider-model-harness)から、次の表で使う経路を選びます。API の4組は設定が整えば選択できる候補であり、手元の資格・接続先・model での実機成功を意味しません。

| provider ID / 接続先 | harness | model と準備条件 |
| --- | --- | --- |
| `claude` / Claude Code の既存経路 | Claude Code（`claude-code`） | CLI のログインと既存 model catalog を使う。新しい汎用 Anthropic API 接続の設定ではない |
| `codex` / Codex CLI の既存経路 | Codex CLI（`codex-cli`） | CLI のログイン・利用権限と版に応じた model catalog を使う |
| `deepseek` / 設定済み DeepSeek backend | `dsh` | 固定 DeepSeek adapter と資格の参照、設定済みの正式 model ID が必要 |
| `deepseek` / 設定済み DeepSeek backend | `hermes` | 明示 profile と資格の参照が必要。公式 DeepSeek の対象 `/anthropic` route は thinking を保持する `/v1` Chat Completions に投影する |
| `strata` / 画面では Local | `dsh` | 既存 adapter に合う API と資格の参照、設定済みの正式 model ID が必要。任意の Local server を保証しない |
| `strata` / 画面では Local | `hermes` | Chat Completions / Anthropic Messages の route を明示設定する。手動 effort の能力公開と、実 Local server の受理・効果の確認は別 |

API を使う4組は現在 macOS の legacy mode 向けです。同じ backend の `models`（未指定なら `model` 1件）にある正式 ID を主・補助 model の候補とし、選んだ model × harness ごとに利用可否を検査します。別 provider の資格や未設定の model を推測して補いません。

[Codex App Bridge](codex-app.md) と [Antigravity / Gemini](antigravity.md) は別の任意統合です。この表から dsh / Hermes との追加の組合せを保証したり、未対応の API を `claude` / `codex` として使えたりするわけではありません。

runtime の任意導入は `install.sh --enable-dsh --dsh-node /absolute/node` / `--enable-hermes` で選びます。[導入と削除](install.md)を参照してください。

## catalog と起動

`dashboard.managed_catalog.standard_catalog(config_file, *, mode)` は標準 spawn の選択肢を返します。`hooks.harness_child.selection()` と同じ世代・設定の静的な検査を使い、model ごとに利用可能性と固定理由を返します。鍵・登録・provider API には接続しません。起動時にも共通 launcher が検査します。

provider ID は deepseek/strata、harness ID は dsh/hermes。manifest の同じ backend の正式 model を主と補助の候補に使い、generator の対応能力に従って公開します。未 opt-in は空配列、legacy 以外は unsupported_mode、Mac 以外は unsupported_platform です。私有 URL・account・実行 path を公開しません。

設定が整って catalog に並んでも、起動や作業の成功は別に確かめます。Telemetry 同梱 NEW AGENT は harness を送らないため、DeepSeek / Local の子を起動できません。cockpit を追加した環境の New Agent / Harness / Lineage と Mail の[通し確認](getting-started.md#5-しりとりで通しの確認をする)を参照してください。画面を使わない起動には [API reference](api.md) の provider・model・harness・親・認証の契約を使います。

## effort の能力と既定

manifestの手書きefforts/default_effortを能力の根拠にはしません。選択modelのharness entryがefforts/default_effort/effort_sourceを持ち、pickerと明示POSTの検証は同じentryを使います。未操作ではeffortを送らず、ハーネス既定を使います。provider共通配列は既定の利用可能harnessからの互換投影だけです。

**dsh**: install/updateは保存済み又は指定した manifestのmodelを対象に、固定Node・runtimeのローカルresolveModelInfoから段と既定を取得します。鍵・profile・discovery・モデルAPIを使わず、runtime世代のevidence/effort-capabilities.jsonへ保存します。runtime/modelが変わったcache・取得失敗はunknown（null/null/unknown）。選択肢だけを無効にし、未指定の起動を妨げません。opt-in offでは取得/投影しません。

dshの報告はsource=harnessで、Strataのserver報告ではありません。固定adapterの4段を自動取得し、Strataの公開sourceでmaxがhighと同じ段であることと、配置版を照合していないことを区別します。

**Hermes**: 固定版の native source をデータとして静的に読み、実効 route に合わせて公開します。Chat Completions の経路では `none`・`minimal`・`low`・`medium`・`high`・`xhigh`・`max`、対象 model の Anthropic Messages の manual thinking 経路では `low`・`medium`・`high`・`xhigh` を使います。manual の4段は Claude・Haiku・Kimi の別方針には適用せず、native の構造が変わった時や読み取れない時は unknown にします。詳しくは[モデルごとの能力](native-capabilities.md)を参照してください。

Hermes の公開既定は `default_effort=null` です。未指定や「Use default」は `--reasoning` を送らず、保存済みの全体・model 別設定や harness の既定に任せます。一定の段を推測表示しません。Chat の `none` と違い、manual 経路の `none` は明示的な thinking 無効化にならないため選択肢にしません。

旧manifestやprivate backendに残る試行low/highは利用者設定の移行対象です。installerはprivate設定を無断書換せず、標準起動の未指定 effort 経路はこれらをハーネス既定として投影しません。

## 現在の制限

- **手動設定**: 共通 provider ログインと OS の秘密保管先を結ぶ機能は設計中です。現行は私有 manifest・backend / profile・資格の参照を利用者が準備します。これらを一式作る初心者向け手順は未整備で、任意導入だけで起動できる状態にはなりません。
- **OS と画面**: API 起動の catalog は macOS の legacy mode に限定されます。WSL2 の既存 Claude Code / Codex CLI 手順や、同梱 NEW AGENT の候補表示を、この経路の対応証拠にはしません。
- **Local effort**: Hermes の manual 4段は要求を組み立てる能力です。実 Local server の受理・効果は未受け入れです。固定版は temperature を1、出力上限を budget+4096 以上にするため、context 制約で拒否される場合があります。fallback して会話が続いたことを、選んだ effort が効いた証拠にしません。
- **記憶と resume**: 現行 Hermes は backend ごとの永続 profile を共有します。子ごとの安定した独立 profile は実装中で、記憶の復帰の実機受け入れは未完です。会話履歴の resume と memory の分離・復帰は別の確認です。旧データの削除や自動移行を案内するものではありません。
- **Telegram**: Hermes の共通 Telegram 入口は設計中です。稼働済みの ORRERY 入口として使えるとは案内しません。
