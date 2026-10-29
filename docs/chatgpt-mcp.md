# ChatGPT から使う MCP 接続（Mac ローカル完結）

> English: [chatgpt-mcp.en.md](chatgpt-mcp.en.md)

`orrery-chatgpt-mcp` は、ChatGPT のカスタム MCP 接続から ORRERY の状態を読み、許可した範囲で新しいエージェントを起動するための小さなゲートウェイです。Desktop Commander などを経由して localhost の HTTP を叩く多段構成の代わりに、ORRERY 専用の MCP ツールを ChatGPT に直接見せます。

**このリポジトリにあるのはサーバー側だけです。** ChatGPT 側で接続を作る操作、OpenAI Platform 側のトンネル権限、ChatGPT の開発者モードはユーザーが行います。ゲートウェイを起動しただけでは ChatGPT に「インストール済み」にはなりません。

## 構成と境界

```text
ChatGPT ──(OpenAI)── Secure MCP Tunnel ◀── tunnel-client（Mac、外向き HTTPS のみ）
                                               │ http://127.0.0.1:8790/mcp/<ペアリング秘密>
                                               ▼
                                  orrery-chatgpt-mcp（127.0.0.1 だけで待受）
                                               │ 固定 allowlist の 6 経路だけ
                                               ▼
                                  dashboard http://127.0.0.1:8770（外へ出さない）
```

- サービス・データ・操作 API はすべてこの Mac の中にあります。公開 HTTPS サーバー、クラウド配備、独自の OAuth 認可サーバー、Cloudflare 等の公開経路は作りません。
- ChatGPT からの通信は OpenAI の [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) を通ります。Mac から張るのは `api.openai.com:443` への外向き接続だけです。**ツールの要求と応答は OpenAI を経由するので、完全オフラインではありません。** ChatGPT に見せた内容（エージェント名、タスクの先頭など）は会話の一部になります。
- ゲートウェイは loopback 以外への bind を拒否します（`0.0.0.0` 不可）。dashboard（8770）と ORRERY Mail（18765）は従来どおり外部に出しません。

## 本人用の権限制御

| 仕組み | 内容 |
| --- | --- |
| ペアリング秘密 | MCP の入口は `/mcp/<43文字の秘密>` だけです。秘密は `orrery-chatgpt-mcp pair` が `~/.agentstack/runtime/chatgpt-mcp/pairing.json`（0600、上書き不可）に作り、tunnel-client の設定にだけ渡します。違う経路は 404 で、推測は回数制限されます。`pair --rotate` は起動中のゲートウェイでも古い URL を即時に無効にします |
| Host / Origin | Host は `127.0.0.1` / `localhost` / `[::1]` と実ポートだけ。Origin 付きの要求（ブラウザ）は全部拒否します。DNS rebinding とブラウザ経由の CSRF を防ぎます |
| Mac 上での承認 | 起動は「プレビュー → Mac で `orrery-chatgpt-mcp approve <コード>` → コミット」です。`approve` はタスク全文・フォルダー・モデルを表示し（制御文字はエスケープ）、承認はそのプレビューと表示したタスク本文のハッシュに結び付きます。プレビューで決まったモデルを固定して起動します。MCP 側から承認を作る手段はありません。タスクに制御文字や双方向上書き文字があるとプレビューで拒否します |
| 起動範囲 | 起動は既定で無効です。有効にしても、作業フォルダーは運用者が登録した別名だけ、provider は `claude` / `codex` だけ、親なし（standalone）、既定は worktree 隔離。`tools`・`base`・`parent` などは受け付けません |
| 重複防止 | コミットには呼び出し側の `idempotency_key` が必須です。同じキーの再送は記録済みの結果を返し、二度目の起動はしません。記録は `spawn-ledger.json`（0600）に残り、再起動後も有効です |
| 結果不明 | 送信後に応答が失われた場合は「unknown」と記録し、成功とは言いません。再送せず `orrery_spawn_status` で照合します |
| 情報の絞り込み | エージェント行は固定の項目だけを返します。ペイン名、コマンド、パス、transcript、Mail 内部、起動ログ末尾は返しません。タスク等の文字列は 300 文字で切り、「信頼しないデータ」と明示します |
| ログ | 1 要求 1 行（主体・登録済みツール名・結果）。引数、秘密、トークンは書きません。dashboard のエラー文はパスや診断を含みうるので ChatGPT へもゲートウェイのログへも出さず、`has_error` やコードだけを返します（詳細は dashboard 自身のログ） |
| 通信の扱い | 拒否した要求の後は接続を閉じます。重複した Content-Length と Transfer-Encoding は拒否。要求の受信（ヘッダーと本文）には 10 秒の絶対期限、同時 32 要求の上限があります。同じ state ディレクトリで動くゲートウェイは 1 つだけです |
| 流用しないもの | ORRERY Mail の owner token、親子の credential、dashboard の operator capability は使いません |

同じ macOS ユーザーで動くプログラムはこの境界の内側です（[ローカル HTTP の信頼境界](local-http-security.md)）。ペアリングファイルを読める手元のプログラムや、`approve` を実行できる手元のエージェントからは守れません。

## ツール

| ツール | 種別 | 内容 |
| --- | --- | --- |
| `orrery_status` | 読み取り | dashboard への到達性、API 世代、この接続でできること |
| `orrery_list_agents` | 読み取り | エージェント一覧（実行中、activity: work / wait / question / ask、モデル、未配送数） |
| `orrery_get_agent` | 読み取り | 1 体の状態 |
| `orrery_spawn_status` | 読み取り | 最近の起動の launching / ready / failed |
| `orrery_spawn_preview` | 起動（dry run） | dashboard の `dry_run` で検証し、`preview_id` と承認コードを返す。何も起動しません |
| `orrery_spawn_commit` | 書き込み | 承認済みプレビューを起動する |

既存エージェントへの指示・メッセージ送信は**この版にはありません**。ORRERY には、エージェントの owner token を借りずに Mail を送る運用者向け API がないためです。tmux への打鍵も使いません。`orrery_status` はこの理由を `message_existing_agent: false` として返します。作業中のエージェントを止める操作（EXIT / kill）、任意コマンド実行、dashboard API の透過プロキシもありません。

ChatGPT のプランによって書き込み系 MCP ツールの扱いが違います（2026-10 時点の OpenAI ヘルプでは Pro は読み取り/fetch、完全な書き込みは Business / Enterprise / Edu のベータ）。ゲートウェイはプランを推測しません。起動を使うかどうかは `ORRERY_MCP_ENABLE_SPAWN` で運用者が決め、ChatGPT 側で使えない場合は読み取りだけで使ってください。

## Mac での手順

1. ペアリング秘密を作る（1 回だけ）。表示された URL は秘密を含むので共有しないでください。

   ```sh
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp pair
   ```

   `AGENTSTACK_PYTHON` で Python 3.11 以上を指定できます。既定のペアリングモードは標準ライブラリだけで動きます（PyJWT は任意の OAuth モードだけで必要）。

2. ゲートウェイを起動する。読み取りだけなら設定は不要です。

   ```sh
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp serve
   ```

   起動も使うときは、作業フォルダーの別名を登録して有効にします。

   ```sh
   ORRERY_MCP_ENABLE_SPAWN=1 \
   ORRERY_MCP_SPAWN_DIRS="notes=/Users/you/notes,web=/Users/you/src/web" \
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp serve
   ```

3. 公式 [tunnel-client](https://github.com/openai/tunnel-client) を入れ、トンネルを作って、MCP の URL に手順 1 の loopback URL を指定します。コマンドの正確な形は `tunnel-client help quickstart` で確認してください。

   ```sh
   tunnel-client init --profile orrery --tunnel-id <tunnel_...> --mcp-server-url "$(~/orrery-telemetry/bin/orrery-chatgpt-mcp url)"
   tunnel-client doctor --profile orrery --explain
   tunnel-client run --profile orrery
   ```

4. ChatGPT で開発者モードを有効にし、カスタム MCP 接続を作ります。接続方式は Tunnel、認証は「認証なし」を選びます（ChatGPT 自身は資格情報を送りません。入口の秘密と Mac 上の承認が本人確認の役目を持ちます）。

5. ChatGPT で起動を頼んだら、Mac で内容を確認して承認します。

   ```sh
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp pending
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp approve K7P2QX9M   # 内容を表示し、yes と入力したときだけ承認
   ~/orrery-telemetry/bin/orrery-chatgpt-mcp deny K7P2QX9M      # 取り消す
   ```

### 常駐させる（launchd、任意）

ログインのたびに手で `serve` しなくて済むよう、ユーザーの LaunchAgent として常駐させられます。

```sh
~/orrery-telemetry/bin/orrery-chatgpt-mcp pair --quiet   # まだなら。秘密は表示しない
~/orrery-telemetry/bin/orrery-chatgpt-mcp service install --source-commit "$(git -C ~/orrery-telemetry rev-parse HEAD)"
~/orrery-telemetry/bin/orrery-chatgpt-mcp service status
```

- `service install` はゲートウェイのパッケージを `~/.agentstack/runtime/chatgpt-mcp/releases/<内容のハッシュ>/` に読み取り専用で複製し、そのコピーを動かします。チェックアウトのブランチを切り替えても常駐中のコードは変わりません。更新するときは目的のコミットで `service install` をやり直します。`--from <src>` で別のチェックアウト（例: `git archive` で取り出した特定コミット）の `integrations/chatgpt_mcp/src` を指定できます。`--source-commit` は記録用のメモで、取り出し元の証明ではありません。
- 起動は `python -I -S -B` です。`PYTHON*` 環境変数、site-packages、`.pth`、`sitecustomize` を読み込まず、バイトコードも書きません。起動のたびに、リリースが「パッケージの `.py` と RELEASE.json だけ・キャッシュなし・シンボリックリンクなし・書き込み不可・自分の所有」であることと、plist に書かれた内容ハッシュを確かめてから読み込みます。合わなければ起動しません（終了コード 78）。
- LaunchAgent（`~/Library/LaunchAgents/org.agentstack.chatgpt-mcp.plist`）に秘密は入りません。秘密は従来どおり 0600 の `pairing.json` から起動時に読みます。
- 常駐版は **127.0.0.1 だけ・起動ツールなし（読み取り専用）** に固定します（`ORRERY_MCP_ENABLE_SPAWN=0`）。起動を使うときは常駐を止めて（`service uninstall`）、手順 2 の `serve` を設定付きで実行します。
- ユーザーの LaunchAgent なので、**ログインしている間**動きます。Mac の起動後はログインした時点で自動起動し、落ちたら 30 秒間隔で再起動します。ログアウト中やログイン前は動きません。ログは `~/.agentstack/runtime/chatgpt-mcp/gateway.log`（0600）。
- Python は指定したパス（既定は `AGENTSTACK_PYTHON`、例: Homebrew の `/opt/homebrew/opt/python@3.13/libexec/bin/python3`）をそのまま使います。標準ライブラリだけで動くので Homebrew のパッチ更新には追従します。そのパスが無くなったら（マイナー版の削除など）起動できなくなるので、`--python` を指定して入れ直してください。
- インストールは、ペアリング前、loopback 以外の dashboard、Python 3.11 未満、ポート使用中、改変・不審なリリース、このコマンドが書いたものではない既存 plist、別の場所から読み込まれた同名ジョブを拒否します。最後に loopback から `orrery_status` を呼んで応答と設定（pairing・起動なし）を確かめます。秘密を含む要求は、`lsof` でその接続を受けたのが launchd のジョブのプロセスだと確かめてから送ります（ポートを横取りした別のプログラムには送りません）。
- `service status` は秘密を表示しません。ジョブが動いていなければ問い合わせません。
- `service uninstall` は plist を確かめてから常駐を止め、launchd から消えたのを確認してから plist を消します。止められなければ失敗として報告し、plist は残します。ペアリング・承認・台帳は残します。
- tunnel-client、API key、ChatGPT 側の設定には触れません。

### Mac だけでの動作確認（トンネル前）

ChatGPT につなぐ前に、Mac の中だけで MCP として答えるかを確かめます。外部には何も出ません。

```sh
URL="$(~/orrery-telemetry/bin/orrery-chatgpt-mcp url)"
curl -s -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"orrery_status","arguments":{}}}' "$URL"
# → "dashboard": {"reachable": true, ...} が返れば、ゲートウェイと dashboard の連携は動いています
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8790/mcp   # → 404（秘密なしでは入れない）
```

トンネル越しの確認は、ChatGPT の接続画面でツール一覧に `orrery_status` などが表示され、`orrery_status` を呼ぶと `reachable: true` が返ることです。ここまで来て初めて「ChatGPT から接続できた」と言えます。

### ユーザーが行う必要があること

- OpenAI Platform の組織で Tunnels の権限（作成者は Read + Manage、tunnel-client 実行者と接続作成者は Read + Use）を得る。組織の owner か RBAC 管理者が付与します。
- ChatGPT の開発者モード（Enterprise / Edu はワークスペース管理者の許可も必要）を有効にする。トンネルは対象の ChatGPT ワークスペースに関連付ける必要があります。
- tunnel-client の runtime API key（`CONTROL_PLANE_API_KEY`）を用意し、リポジトリには入れない。
- ChatGPT のプランが書き込みツールに対応しているか確認する。

Secure MCP Tunnel がアカウントで使えない場合、代わりに公開する方法は取りません。loopback のゲートウェイは手元の MCP クライアントで使えますが、ChatGPT からは接続できない、という状態のままです。

## 失敗時の答え方

| 状況 | 結果 |
| --- | --- |
| 秘密のない経路・違う秘密 | HTTP 404（回数超過で 429） |
| loopback 以外の Host、Origin 付き | HTTP 403 |
| dashboard が止まっている・壊れた応答 | ツール結果 `isError: true`、`dashboard_unavailable`。`orrery_status` は `reachable: false` |
| 起動が無効・範囲外の引数 | `unknown_tool` / `invalid_arguments` |
| 未承認でコミット | `approval_required`（プレビューは消費しない） |
| 送信中・送信後に応答なし | `outcome_unknown`。同じキーの再送は送り直さず照合結果を返す（接続できなかったときだけ「未送信」） |
| JSON-RPC として不正・応答メッセージ・batch | HTTP 400 |

## 設定

| 変数 | 既定 | 内容 |
| --- | --- | --- |
| `ORRERY_MCP_PORT` | `8790` | 待受ポート（127.0.0.1） |
| `ORRERY_MCP_DASHBOARD_URL` | `http://127.0.0.1:8770` | loopback の dashboard だけ |
| `ORRERY_MCP_STATE_DIR` | `~/.agentstack/runtime/chatgpt-mcp` | ペアリング・承認・台帳 |
| `ORRERY_MCP_ENABLE_SPAWN` | `0` | `1` で起動ツールを出す |
| `ORRERY_MCP_SPAWN_DIRS` | なし | `別名=/絶対パス` をカンマ区切り |
| `ORRERY_MCP_SPAWN_APPROVAL` | `local` | `chat` にすると Mac での承認を省き、ChatGPT 上の確認だけになる（推奨しません） |
| `ORRERY_MCP_RATE_PER_MINUTE` | `60` | 1 分あたりの要求数 |

### 任意: OAuth モード

`ORRERY_MCP_AUTH=oauth` にすると、ペアリングの代わりに外部の認可サーバーが発行した OAuth 2.1 アクセストークンを検証します（issuer / audience / 期限 / 署名 / scope `orrery.read`・`orrery.spawn` / 許可した `sub`）。ゲートウェイは認可サーバーにはなりません。ローカル完結の初期構成では使いません。既存の IdP をトンネル経由で使う場合のために残しています。設定は `ORRERY_MCP_RESOURCE`、`ORRERY_MCP_ISSUER`、`ORRERY_MCP_JWKS_URL` または `ORRERY_MCP_JWKS_FILE`、`ORRERY_MCP_ALLOWED_SUBJECTS` です。

## 確認

```sh
PYTHONPATH=. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests
```

未認証・誤 scope・偽トークン・Host/Origin・注入・重複起動・結果不明・dashboard 停止・公式 MCP SDK クライアントでの E2E を含みます。ChatGPT 本体との接続はユーザー側の設定が必要なため、このテストでは確認していません。
