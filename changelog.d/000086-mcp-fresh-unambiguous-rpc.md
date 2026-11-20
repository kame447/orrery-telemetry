### fresh MCP実プロセス検証で曖昧なJSON-RPC応答を拒否

実プロセスのMCP応答で、同一JSONオブジェクトの重複キーをすべての階層で拒否する。JSON-RPCのIDも整数型のみ許可し、真偽値を整数扱いして通す不具合を防ぐ。生応答や秘密を露出させず、実サービス・本番設定を変更しない。修正前に失敗した3つの合成回帰テストを追加し、修正後の対象26件を確認した。API generationは変更しない。

詳細: [日本語](../docs/mcp-fresh-unambiguous-rpc.md) / [English](../docs/mcp-fresh-unambiguous-rpc.en.md)
