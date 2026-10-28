### ChatGPT から使う MCP ゲートウェイ `orrery-chatgpt-mcp` を追加（Mac ローカル完結・既定オフ）

MCP Streamable HTTP のゲートウェイを別プロセスとして追加しました。127.0.0.1 だけで待受し、ChatGPT からは OpenAI の Secure MCP Tunnel（Mac からの外向き接続）を通して使います。入口はペアリング秘密を含む経路だけで、Host は loopback、Origin 付きの要求は拒否します。読み取りツールは固定の項目だけを返します。エージェントの起動は既定で無効です。有効にしても、登録したフォルダーの別名、standalone、dry run のプレビュー、Mac 上での `approve`、idempotency key が必要で、結果不明は成功とせず照合します。既存エージェントへのメッセージ送信は、owner token を借りない方針のため未対応です。dashboard・Mail・CLI の API と挙動は変えていません。ChatGPT 側の接続作成、トンネル権限、開発者モードはユーザーの設定が必要で、ChatGPT からの実接続はまだ確認していません。[docs/chatgpt-mcp.md](../docs/chatgpt-mcp.md)
