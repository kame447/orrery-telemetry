### 隔離fresh MCPの不明な起動・停止結果を再送・再起動なしで観測

段階15として、専用fixtureの固定plist、launchdジョブ状態、実PIDとMCP権限を読み取りだけで再確認するAPIを追加しました。以前のPIDが存在する、ジョブの所有が不明、途中で設定が変わる場合は拒否します。未ロードを「安全に再起動可能」「Mailは未送信」の証拠とせず、観測結果から自動bootout/bootstrapや再送を行いません。公開MCPツールとDashboard API世代は不変です。実Macでの復旧・接続受入は未検証です。詳細は[不明結果の読み取り照合](../docs/mcp-fresh-fixture-recovery.md)を参照してください。
