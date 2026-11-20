### 固定releaseのfresh MCPで隔離受信者のread/ackを受入検証

Macの専用LaunchAgentで実MCP SDKから正規Mail受理まで確認後、認証済み試験用受信者が`fetch_inbox`で同一メッセージを取得し、read/ackを記録する受入試験を追加。送信側statusは処理開始を推測せず`unobserved`を保持。実agentの受信・作業開始やTunnel接続、本番切替の証明ではない。詳細は[受信者の隔離観測](../docs/mcp-fresh-pinned-recipient-observation.md)。
