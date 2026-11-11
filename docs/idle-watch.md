# project idle watch（設計メモ）

> English: [idle-watch.en.md](idle-watch.en.md)

project の全エージェントが work でなくなったとき、dashboard が稼働中の親へ ORRERY Mail で知らせます。opt-in で、既定は無効です。親を resume することはありません。これは補助機能であり、「子が完了したら親が次を配る」運用そのものを保証するものではありません。

## 調査結果

- **「入力欄が空か」で再開する仕組みは、このリポジトリに見つかりませんでした。** `dashboard/`、`bin/`、`hooks/`、daemon、persistent-agent を検索しました。Laplance 側にあると思われ、触っていません。
- **`act_state`**（`work` / `wait` / `ask`）は、稼働中セッションごとの tmux `capture-pane` 1 回から読みます（`_agent_runtime`、`_parse_runtime`、4.5 秒キャッシュ）。`/api/agents` で返ります。`harness_activity.parse_activity` は dsh/Hermes だけが対象です。`None` は「判定不能」であり、idle ではありません。行ごとの時刻はなく、payload の `ts` だけがあります。
- **一部の行は `project_key` が `null` です**（project 外で起動した親、複数 project に同名で登録された名前など）。所属は `project_key` が一致する行です。設定された親（名前指定）もその中にいる必要があります。
- **正規の起こし方:** 親への ORRERY Mail `send_message`（mail watcher が配送）。tmux への直接入力はしません。

## 動作

サンプルは次の 4 種類です。`busy`（メンバーに work が 1 体でもいる）、`idle`（全員 wait）、`blocked`（work なし・ask あり）、`unknown`。動くのは `idle` と `blocked` だけで、`samples` 回連続かつ `seconds` 秒以上続いたときです。

次の場合は `unknown` であり、何も送りません（fail-closed）。

- 親の行が無い、または稼働していない（終了・retire 済みの親は resume しません）。
- 親の名前の行の `project_key` がこの project ではない（別 project、または未登録・複数 project に同名登録のため `null`）。
- メンバーの行が `running: false`（生存を確認できない）または `retired_but_alive`。
- `project_key` が `null` で ORRERY Mail に登録された行（同名が複数 project にある等）が、稼働中の `wait` 以外。この project の子かもしれないためです。Mail に登録されていない tmux セッションは対象外です。
- `act_state` が work/wait/ask 以外か欠けている。取得失敗。`ts` が `stale` 秒より古い、または未来。

各区分の結果:

- `idle`: その親の子のうち、最後に work だった検証済みの子の identity から、親へ Mail を送ります。検証するのは、tmux の `PARENT_AGENT` が親と一致すること、child state の名前・project・親が一致すること、runtime の owner token が child state の token と一致することです（provider 停止通知が行う tmux generation・登録 agent_id・program の照合はしていません）。送信直前に `/api/agents` を取り直し、同じメンバーで `idle` のままでなければ送りません。件名と本文には、dashboard の自動通知であり送信元の子が書いたものではないことを明記し、各エージェントの状態と継続秒数を添付します。
- 再送: 初回送信が成功（`ok`）した場合だけ、同じ idle が続く間 `resend_after` × 2^k（上限 `resend_cap`）の間隔で、最大 `max_resends` 回まで再送します。既定では初回 1 回と再送最大 3 回です。送信結果が不明（timeout 等）、拒否、検証できる子がいない、送信直前に状態が変わった、例外のいずれかなら、それ以降は自動で送りません。
- 送信回数の予算は、メンバーの `work` を観測したときだけ戻ります。`unknown` や `blocked` を挟んでも戻りません。
- `blocked`: 送りません。blocked 区間ごとに、デスクトップ通知（macOS `osascript`）とログを 1 回出します。
- 状態はメモリ上にあり、dashboard を再起動すると最初から判定し直します。

## 設定

`$AGENTSTACK_RUNTIME_DIR/idle-watch.json`。無ければ無効です。dashboard の起動時に 1 回だけ読みます。変更を反映するには dashboard の再起動が必要です。legacy mode だけで動き、global mode と fresh mode では動きません。

既定値は `samples` 3、`seconds` 60、`poll` 10、`stale` 30、`resend_after` 600、`max_resends` 3、`resend_cap` 3600 です。値はすべて整数で、範囲は `samples` 1–1000、`seconds` 0–86400、`poll` 1–3600、`stale` 1–3600、`resend_after` 1–86400、`max_resends` 0–10、`resend_cap` 1–86400 です。範囲外・小数・文字列・bool・NaN の値や空の `parent` を含む project は、警告をログに出して無効になります。`projects` が object でなければ、機能全体が無効になります。

```json
{"projects": {"/abs/project": {"enabled": true, "parent": "ParentName"}}}
```

identity は作りません。

## 制限

- 親が終了していると何もしません。親の再開は人が行ってください。
- 親がこの project に登録されていない場合や、親の名前が別 project にも登録されている場合は発火しません。
- 判定は tmux 画面から読んだ `act_state` に依存します。画面から判定できないエージェントがいる project では発火しません。
- 状態遷移と送信呼出しは、Mail を mock にしたテストで確認しています。実際の Mail での送受信は検証していません。

## dry-run

ソースの checkout から実行します（installer はこのツールをコピーしません）: `bin/agentstack-idle-watch --project KEY --parent NAME [--samples N --seconds T --poll S --iterations K]` は、稼働中の `/api/agents` を取得し、区分と各エージェントの状態をログに出し、送るはずの内容を表示します。送信も resume もしません。範囲外の値は拒否します。
