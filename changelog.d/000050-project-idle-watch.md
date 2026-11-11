### 全エージェントが非稼働になった project の親へ、Mail で知らせる opt-in 機能

`$AGENTSTACK_RUNTIME_DIR/idle-watch.json` で有効にした project について、親とメンバー全員が `wait` の状態が連続 3 回・60 秒以上続くと、検証済みの子の identity から稼働中の親へ ORRERY Mail を送ります。本文には dashboard の自動通知であることを明記します。親が稼働していなければ何もせず、resume はしません。承認待ち（`ask`）のエージェントがいる間は送らず、デスクトップ通知を 1 回出します。

所属や生存を確認できない行、`act_state` が取れない・古い・未来のデータは idle と見なしません。送信直前に状態を取り直します。再送は初回送信が成功した場合だけで、上限付きの backoff です（既定は再送最大 3 回）。結果が不明なら自動で再送しません。範囲外の設定値を持つ project は無効になります。legacy mode だけで動き、設定は dashboard 起動時に読みます。既定は無効です。判定を試す `bin/agentstack-idle-watch` は dry-run 専用で、何も送りません。設計と制限は [project idle watch](../docs/idle-watch.md) を参照してください。
