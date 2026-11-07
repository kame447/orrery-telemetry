# project idle watch（設計メモ）

> English: [idle-watch.en.md](idle-watch.en.md)

project の全エージェントが work でなくなったとき、dashboard が親を 1 回だけ起こします。opt-in で、既定は無効です。

## 調査結果

- **「入力欄が空か」で再開する仕組みは、このリポジトリに見つかりませんでした。** `dashboard/`、`bin/`、`hooks/`、daemon、persistent-agent を検索しました。Laplance 側にあると思われ、触っていません。
- **`act_state`**（`work` / `wait` / `ask`）は、稼働中セッションごとの tmux `capture-pane` 1 回から読みます（`_agent_runtime`、`_parse_runtime`、4.5 秒キャッシュ）。`/api/agents` で返ります。`harness_activity.parse_activity` は dsh/Hermes だけが対象です。`None` は「判定不能」であり、idle ではありません。行ごとの時刻はなく、payload の `ts` だけがあります。
- **一部の行は `project_key` が `null` です**（project 外で起動した親など）。そのため所属は、`project_key` が一致する稼働中の行と、設定された親（名前指定）にしました。project 不明の行は無視します。
- **正規の起こし方:** 親への ORRERY Mail `send_message`（mail watcher が配送）。親のセッションが gone なら `do_jump`（`agentstack-resume` と同じ経路）。tmux への直接入力はしません。

## 動作

サンプルは次の 4 種類です。`busy`（work が 1 体でもいる）、`idle`（全員 wait）、`blocked`（work なし・ask あり）、`unknown`（その他や欠損の状態、メンバー 0、取得失敗・stale）。動くのは `idle` と `blocked` だけで、`samples` 回連続かつ `seconds` 秒以上続いたときです。

- `idle`: `sender` から親へ Mail を 1 通送ります（`source: idle-watch` を明記し、各エージェントの状態と継続秒数を添付）。親が稼働していなければ、代わりに resume します。再送は `resend_after` × 2^k（上限 `resend_cap`）で、最大 `max_resends` 回です。
- `blocked`: 再開しません。blocked 区間ごとに、デスクトップ通知（macOS `osascript`）とログを 1 回出します。
- 区分が変わる（work 復帰、ask の出現・解消、unknown）と区間は終わり、再び発火できる状態に戻ります。状態はメモリ上にあり、dashboard を再起動すると最初から判定し直します。

## 設定

`$AGENTSTACK_RUNTIME_DIR/idle-watch.json`。無ければ無効です。既定値は `samples` 3、`seconds` 60、`poll` 10、`stale` 30、`resend_after` 600、`max_resends` 3、`resend_cap` 3600 です。

```json
{"projects": {"/abs/project": {"enabled": true, "parent": "ParentName", "sender": "WatcherName"}}}
```

`sender` は登録済みの Mail identity で、owner token が runtime ディレクトリにあるものです。watcher は identity を登録しません。未設定なら、Mail は警告を出して送りません。

## dry-run

`bin/agentstack-idle-watch --project KEY --parent NAME [--samples N --seconds T --poll S --iterations K]` は、稼働中の `/api/agents` を取得し、区分と各エージェントの状態をログに出し、送るはずの内容を表示します。送信も resume もしません。
