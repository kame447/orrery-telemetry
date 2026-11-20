### fresh MCPの実効権限status本文二形式の不一致を拒否

`orrery_status`の`structuredContent`だけでなく、MCPクライアントが表示する`content`内のJSONも型を含め一致確認し、矛盾したruntime modeや送信・spawn権限、認可先を検証済みと誤報しない。専用テストのみで本番変更なし。詳細は[status本文二形式の一致検証](../docs/mcp-fresh-status-dual-content.md)。
