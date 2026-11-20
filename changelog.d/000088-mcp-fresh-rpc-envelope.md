### fresh MCPの実プロセス検証で矛盾したJSON-RPC応答を拒否

実MCP応答に成功の `result` と失敗の `error` が同時に存在するとき、成功側だけを読み取ってサービス・権限を検証済みにしていた問題を修正。initialize、tools/list、orrery_statusを矛盾した応答で成功扱いしない。既定OFF、本番MCP、Mail、Tunnel、agentの状態を変更せず、公開API generationも据え置く。

詳細: [日本語](../docs/mcp-fresh-rpc-envelope.md) / [English](../docs/mcp-fresh-rpc-envelope.en.md)
