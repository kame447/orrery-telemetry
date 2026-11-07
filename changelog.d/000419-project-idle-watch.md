### 全エージェントが非稼働になった project の親を、1 回だけ起こす opt-in 機能

`$AGENTSTACK_RUNTIME_DIR/idle-watch.json` で有効にした project について、稼働中の全エージェントが `wait` の状態が連続 3 回・60 秒以上続くと、検証済みの子から親へ ORRERY Mail を 1 通送ります。親のセッションが無ければ、代わりに resume します。承認待ち（`ask`）のエージェントがいる間は再開せず、デスクトップ通知を 1 回出します。

`act_state` が取れない、または古いデータは idle と見なしません。再送は上限付きの backoff です。既定は無効です。判定を試す `bin/agentstack-idle-watch` は dry-run 専用で、何も送りません。設計は [project idle watch](../docs/idle-watch.md) を参照してください。
