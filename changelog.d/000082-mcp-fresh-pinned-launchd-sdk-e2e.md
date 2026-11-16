### 固定releaseのfresh MCPを隔離Mac LaunchAgentで起動し実SDK・Mailを検証

専用LaunchAgentの実PID・plist・TCP接続所有者を照合後、固定gateway/Mail/第三者依存の実MCPをSDKで接続して、preview・Mail受理・receipt・二重送信防止・完全な登録解除を試験する。既定ではskipし、本番や実agent実行の受入とは区別する。API generation変更なし。

詳細: [日本語](../docs/mcp-fresh-pinned-launchd-sdk-e2e.md) / [English](../docs/mcp-fresh-pinned-launchd-sdk-e2e.en.md)
