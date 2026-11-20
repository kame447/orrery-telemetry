### fresh MCPの専用LaunchAgent plistを既存サービスを変えずに準備する

運用者が承認した固定release・Mail instance・送信者/宛先を事前照合し、専用plistを一度だけatomic no-clobberで作成します。既存job・ファイルを上書きせず、起動/停止・fresh本番切替はしません。依存Pythonと起動時release検証は次段階です。[利用条件](../docs/mcp-fresh-service-staging.md)。
