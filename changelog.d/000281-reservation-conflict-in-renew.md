### 予約の更新応答で編集前の衝突を確認

予約 guard が非公開の Mail resource を呼び、自己予約があっても編集を拒否する不具合を修正しました。Mail は予約の更新と他者の有効な排他予約の照合を同じ transaction で行い、Claude と Codex の guard は同じ応答の検査範囲と結果を確認します。hook 側の pattern 判定の写しを削除しました。

Mail の更新を先に行ってから hook を配備してください。新 hook は旧 Mail の検査結果のない応答を拒否します。旧 hook（#269 より前）は新 Mail でも従来どおり動きます。到達した Mail の照会拒否や不正な検査結果は編集前に拒否し、renew は redirect を追わず、HTTP 応答後の本文読取失敗も拒否します。応答前の通信不能は identity の有無によらず既存の outage policy に従います。実サービスの切替は別途行ってください。
