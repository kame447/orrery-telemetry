### fresh MCPの隔離Dashboard接続先をplanと承認digestに固定

固定release用のfresh MCP起動コマンドとLaunchAgent設定に、検証済みのloopback Dashboard URLを明示指定できるようにした。既定の8770は維持し、隔離用の専用ポートを選択した場合は起動引数・plist・実状態検証・承認digestで同一値を使用する。fixtureの明示起動時は既定8770への接続も拒否する。外部host、URL認証情報、曖昧なURL、MCP自身への接続を拒否し、別接続先へ切り替えたplistが受け入れられないことをテストで確認した。固定release実稼働・Tunnel・agent受信の完成を意味しない。

詳細: [日本語](../docs/mcp-fresh-dashboard-loopback.md) / [English](../docs/mcp-fresh-dashboard-loopback.en.md)
