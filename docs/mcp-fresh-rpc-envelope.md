# fresh MCP実プロセス照合のJSON-RPC応答矛盾を拒否する（fork段階32）

fresh専用LaunchAgentを認証済みTCP接続で検証する際、従来の `fresh_process_probe._request` はJSON-RPC応答が `result` と `error` を同時に含んでいても `result` のみを採用していた。JSON-RPC 2.0では成功と失敗の両方を同じ応答に記載してはいけない。矛盾した応答を成功扱いすると、実MCPの初期化・ツール一覧・実効送信権限が検証済みであると誤報する。

今回、応答のトップレベルに `error` があれば、値が何であっても `MCP_PROTOCOL_UNVERIFIED` として拒否する。MCP状態、実サービス、配信権限、既存の送信契約は変えない。secret・本文はログ出力しない。

Mac Python 3.13の合成loopbackで、initialize、tools/list、orrery_status（tools/call）の3箇所で矛盾した応答が修正前には拒否されなかったことを確認し、修正後はすべて拒否した。対象unittest 28件成功。GitHub CIと独立レビューは別途確認する。専用Tunnel経由のChatGPT実接続、実agentの処理開始・完了、本番identity/allowlistの承認はこの段階では実証していない。

ロールバックは本段階のコード、テスト、日英文書、changelog fragmentをrevert。既定OFFと本番MCP/Mail/Tunnel/agentは変更せず、公開Dashboard API generationも変更しない。

対象コマンド: `python3 -m unittest integrations.chatgpt_mcp.tests.test_fresh_process_probe -q`。
