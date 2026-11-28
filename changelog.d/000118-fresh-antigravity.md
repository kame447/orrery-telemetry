### Projectless fresh runtime に Antigravity を接続

fork の fresh runtime で Antigravity（`agy`）を本人登録、親あり・親なしの New Agent、ファイル予約、Mail、同じ会話の再開へ接続します。追加の Mail は同じ native stream に渡し、通知の引渡しだけで既読や ACK を付けません。 同じ会話の再開では実際の PreInvocation を現在の起動へ結び付け、復元された過去のフックを本人確認の代わりに使いません。終了はこの起動の native process group を確認してから予約を解放します。

実機で一部の Mail ツールが有効にならなかった長い MCP 名を短縮し、全 UUID による識別を保ちます。既存ログインと権限は変更しません。`write_file`／`edit_file` と従来の編集ツールの対象パスをそれぞれ検査し、未予約・他人予約の編集を拒否します。専用の `notebook_edit`／`sed_file` は対象パスの契約を確認できていないため拒否します。対応範囲と残る移行条件は [Antigravity ガイド](../docs/antigravity.md) と [fresh 移行手順](../docs/fresh-global-update.md) を確認してください。本変更による API generation の変更はありません。
