# fresh MCPツールの公開メタデータを完全照合（fork段階40）

fresh専用の実プロセスprobeは、これまで6ツールの名前・引数schema・権限annotations・securitySchemesを照合していましたが、`tools/list`の追加フィールドや表示用の`title`・`description`の型までは検証していませんでした。つまり、同じ6ツールでも`execution`によるタスク実行機能の宣言、`outputSchema`や`_meta`など未知の公開契約を追加した応答を受理できました。

本段階では、forkの固定ツール定義の各項目が`name`、`title`、`description`、`inputSchema`、`annotations`、`securitySchemes`の6フィールドだけであることを検証し、`title`と`description`を空でない文字列に限定します。説明文の内容までは固定しません。未知フィールドは仕様追加の可能性があっても黙認せず、利用者への権限・公開機能変更のレビューを要する失敗として扱います。probeだけの変更で、通常MCPサーバーのツールや権限は増減しません。

隔離loopbackで変更前に9ケースの誤受理（未知の5フィールド・説明メタデータの不正4ケース）を再現しました。修正後は同じケースを拒否し、標準の定義で実プロセス検証が成功します。固定した`inputSchema`・送信権限・永続idempotencyおよび既存のlegacy運用は変更しません。

前提はfork Draft PR #95です。変更をrevertすると追加のツールメタデータを見逃す従来の検証に戻ります。実ChatGPTの専用Tunnel接続、実agentの処理開始・作業完了、本番identity/allowlist承認、upstream受入は本段階の検証対象外です。本番のGateway/Mail/Tunnel/稼働agentとfork master、upstreamは変更しません。
