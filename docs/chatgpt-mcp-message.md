# ChatGPT から既存エージェントへ追加指示を送る

> English: [chatgpt-mcp-message.en.md](chatgpt-mcp-message.en.md)

[ChatGPT 用 MCP 接続](chatgpt-mcp.md)に、稼働中の ORRERY エージェントへ**ORRERY Mail で**追加指示を送るツールを足す設定です。**既定では無効**です。運用者が Mac で専用の送信者を作り、宛先を明示的に許可したときだけ使えます。

## 仕組み

```text
ChatGPT ─ Secure MCP Tunnel ─ orrery-chatgpt-mcp（127.0.0.1）
                                  │ orrery_message_preview / commit / status
                                  ▼
                       DaemonRuntime（専用 sender「orrery-daemon」の owner credential）
                                  │ 正規の send_message（宛先は許可リストだけ）
                                  ▼
                       ORRERY Mail ─▶ 宛先エージェントの受信箱
```

- **送信者は専用の Mail identity です。** 運用者が `agentstack-daemon create --operator` で作る `program=orrery-daemon` の登録を使います（[決定的 daemon](daemon-agents.md)）。既存エージェントの owner token を借りること、送信者名を偽ること、tmux への打鍵、Mail DB の直接操作は一切しません。
- **宛先の許可リストは daemon のプロファイル（`orrery-daemon-binding-v2`）の `peers` です。** 名前と数値 Mail ID の組を 1〜16 件、運用者が書きます。ChatGPT から許可リストを変える手段はありません。
- **確認すること。** 送信の直前ごとに、Mail の管理ソケットと HTTP の両方で Mail インスタンスの UUID、送信者自身の行・credential の指紋と世代・`program`、宛先の数値 ID・プロジェクト・退役状態を照合します（`DaemonRuntime`）。ゲートウェイはさらに、プレビュー時に見た宛先の ID・program・プロジェクト ID・登録時刻と、送信者・インスタンスが変わっていないことを確かめます。名前の再利用、同名の別プロジェクト、退役、存在しない宛先は送らずに拒否します。
- **照合の限界。** これらは送信要求の直前の照合で、Mail の配送トランザクションと一体ではありません。最後の照合と送信のあいだに、宛先が自分の owner token で再登録して program などを変えることは防げません（数値 ID が変わる置き換えは、送信直前の数値 ID の照合で拒否します）。
- **本文の検査。** 制御文字（C0/C1）、双方向上書き、ゼロ幅・不可視の書式文字、タグ文字、私用領域、行/段落区切り文字を含む本文や件名はプレビューで拒否します。Mail は Markdown の画像参照 `![..](..)` を書き換え（参照先のファイルも読みます）、重要度も送信者の経路では指定できないため、画像参照は拒否し、重要度は常に `normal` です。ChatGPT に見せた件名・本文と受信箱に届く件名・本文は一致します（E2E で確認）。

## ツール

| ツール | 種別 | 内容 |
| --- | --- | --- |
| `orrery_message_preview` | 準備 | 宛先・件名・本文全文・送信者・Mail ID・`content_sha256` を返します。送信はしません |
| `orrery_message_commit` | 書き込み | `preview_id`・`idempotency_key`・`content_sha256` が揃ったときだけ 1 回送ります。Mail の受領記録（メッセージ ID、送信者、宛先、プロジェクト、スレッド、件名）が一致したときだけ accepted です |
| `orrery_message_status` | 読み取り | 記録した結果（accepted / rejected / unknown）と、Mail 上の既読・ACK |

`orrery_status` の `capabilities.message_recipients` に許可された宛先名が出ます。

本文は次の形で届きます。宛先は誰からの指示かを末尾で確認できます。

```text
<ChatGPT で確認した指示>

---
Sent by the operator from ChatGPT through orrery-chatgpt-mcp as <送信者名>. orrery-mcp-ref: <16 桁の参照>
```

件名は `[ChatGPT] <件名>`、スレッドは `orrery-mcp-<参照>` です。

## 確認の境界（スマホからの利用）

既定の `ORRERY_MCP_MESSAGE_APPROVAL=chat` では、毎回 Mac に戻る必要はありません。境界は次の組み合わせです。

1. 送れる相手は、運用者が Mac で書いたプロファイルの宛先だけ。
2. コミットにはプレビューの `content_sha256` が必要です。ChatGPT がユーザーに見せたプレビューと違う内容は送れません。`orrery_message_commit` は `destructiveHint: true` なので、ChatGPT は実行前にユーザーの確認を求めます。
3. 1 時間あたりの送信数の上限（既定 20、`ORRERY_MCP_MESSAGE_MAX_PER_HOUR`）。
4. 送信の監査記録 `message-audit.jsonl`（0600、本文なし）。
5. 緊急停止: プロファイルの `pause_path` にファイルを置くと、次の送信から拒否します。

毎回 Mac で承認したい場合は `ORRERY_MCP_MESSAGE_APPROVAL=local` にします。プレビューが承認コードを返し、`orrery-chatgpt-mcp approve <コード>` が宛先・件名・本文全文（制御文字はエスケープ）を表示します。承認はそのプレビューと本文のハッシュに結び付きます。

## 重複送信を防ぐ

- 同じ `idempotency_key` の再コミットは、記録済みの結果を返すだけで再送しません。別のメッセージに同じキーは使えず、1 つのプレビューは 1 つのキーでしか送れません。キーの記録は期限で消しません（state ディレクトリがある限り残ります）。
- 記録 `message-ledger.json`（0600、本文なし）は再起動後も有効で、同じ state ディレクトリを使う別プロセスともファイルロックで直列化します。送信者プロファイルのロックは排他なので、同時に 2 つ送ることもありません。
- プレビューはメモリだけに置きます。ゲートウェイが再起動したら、プレビューからやり直してください（安全側の失敗です）。
- 送信要求を出した後に応答が失われた場合、Mail の受領記録が想定と違う場合は **unknown** と記録し、成功とは言わず、自動で再送もしません。Mail が保存前に拒否したと明示した場合（ファイル予約の衝突）だけ rejected です。
- `orrery_message_status` は dashboard の Mail 表示（`/api/edge-messages`）で、送信者・宛先の名前に加えて、このメッセージのスレッドと本文全体（ランダムな参照番号を含む）が完全に一致する行を探し、見つかれば accepted に更新します。引用や別インスタンスの同名・同 ID の行は一致しません。見つからなければ unknown のままです。
- 送信後に記録の書き込みだけが失敗した場合は `outcome_unrecorded`（再送しないこと）を返します。2 分を過ぎても終わらない送信記録は unknown（`dispatch-unfinished`）として表示します。

## 状態の意味

| 状態 | 意味 |
| --- | --- |
| `accepted` | Mail が受理し、宛先の受信箱に入りました（Mail のメッセージ ID つき） |
| `read` / `acknowledged` | 宛先が Mail 上で既読/ACK にしました（dashboard で見える場合だけ） |
| `rejected` | 送っていません（理由コードつき） |
| `unknown` | 送ったかどうか分かりません。再送しないでください |

**エージェントがその指示で作業を始めたか・終えたかは、このゲートウェイからは分かりません。** Mail の受理・既読と、TUI のターン処理は別のものです（汎用の TUI 投入は Issue #32 の範囲です）。

## Mac での設定（運用者が行う）

1. 送信者を作る。稼働中の Mail に新しい identity を登録する操作なので、運用者が自分で実行します（エージェントには実行させないでください）。

   ```bash
   <Mail の Python> <ORRERY>/bin/agentstack-daemon create \
     --connection <接続ファイル> --project-key <プロジェクト> \
     --name ChatRelay --journal <journal> --operator
   ```

   接続ファイルとプロファイルの書き方は [決定的 daemon](daemon-agents.md) を参照してください。
2. プロファイル（0600）の `peers` に、指示を送ってよいエージェントの名前と数値 Mail ID を書きます。ID は whois の正規の応答から取ります。`agentstack-daemon inspect --profile <プロファイル>` で検証できます。
3. ゲートウェイの環境変数を設定して起動します。

| 変数 | 既定 | 内容 |
| --- | --- | --- |
| `ORRERY_MCP_ENABLE_MESSAGE` | 未設定（無効） | `1` で有効 |
| `ORRERY_MCP_MESSAGE_PROFILE` | なし | 送信者プロファイルの絶対パス（有効時に必須。無効時に設定すると起動を拒否） |
| `ORRERY_MCP_MESSAGE_APPROVAL` | `chat` | `chat` または `local` |
| `ORRERY_MCP_MESSAGE_MAX_PER_HOUR` | `20` | 1〜200 |
| `ORRERY_MCP_MESSAGE_HELPER_ROOT` | 実行中のチェックアウト | `DaemonRuntime` を読む ORRERY のチェックアウト（本人所有で group/other 書き込み不可） |

ゲートウェイの Python に SQLAlchemy は要りません（管理ソケットの定数を `agentstack_mail.enrollment_protocol` に分けました）。

`ORRERY_MCP_MESSAGE_HELPER_ROOT` は、読み込む前に次をすべて確かめます。チェックアウトの上位ディレクトリが他人に書き込めないこと（root 所有の sticky ディレクトリは可）、`integrations/codex_app/src` と `packages/agentstack_mail/src` の中のすべてのディレクトリ・ファイルが本人か root の所有で group/other 書き込み不可、シンボリックリンクなし。2 つのパッケージがその場所から読まれること、別の場所の同名パッケージが読み込み済みでないこと。満たさなければ何も実行せずに拒否します。

### ログについて

ゲートウェイの記録（ledger、監査、ログ）に本文は入りません。ただし ORRERY Mail 自身のツールログ（既定で有効）は、この機能に限らずすべての `send_message` の引数と結果を記録し、本文も含みます。本文を Mail のログにも残したくない場合は、Mail 側で `AGENTSTACK_MAIL_TOOLS_LOG_ENABLED=false` にしてください。Mail のログで本文を伏せる変更は別の課題です。

### launchd 常駐版との関係

`orrery-chatgpt-mcp service install`（PR #42）が固定する読み取り専用リリースはゲートウェイのソースだけを持ち、送信を有効にする環境変数も渡しません。**常駐版では送信は無効のままです。** 常駐版で送信を使えるようにするのは別の変更です。それまでは手動起動のゲートウェイで試してください。

## 試験

- `integrations/chatgpt_mcp/tests/test_chatgpt_gateway_message.py`: 偽の送信者での契約試験（既定無効、本文検査、宛先変更、digest 不一致、キーの使い回し、結果不明時の照合、時間あたり上限、再起動、並行コミット、Mac 承認、監査記録に本文がないこと）。
- `integrations/chatgpt_mcp/tests/test_chatgpt_gateway_message_e2e.py`: 一時ディレクトリで別の Mail プロセスを立て、架空のエージェントだけで、ゲートウェイの HTTP MCP 経由で送信→受信箱で 1 通だけ受け取ることを確かめます。許可外の宛先、プレビュー後の退役、送信停止、Mail 停止で送らないことも確かめます。稼働中の Mail・dashboard・launchd・トンネルには触れません。

実際の ChatGPT とトンネル経由での送信は、ユーザー側の設定（Platform 権限、開発者モード）が済むまで確認できていません。
