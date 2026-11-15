### fresh MCPの実プロセスと送信権限を照合する検証部品を追加

後続の独立したfresh常駐化に向け、launchdの実PIDと同一TCP接続のsocket所有者を確認してからMCPの実際のツール一覧・Mail instance・送信者と宛先の許可を照合できる部品を追加しました。接続切断後の自動再接続による秘密送信を拒否します。既存サービスの導入・更新・停止は行いません。Dashboard API世代は変更しません。[検証条件と制限](../docs/mcp-fresh-process-probe.md)を参照してください。
