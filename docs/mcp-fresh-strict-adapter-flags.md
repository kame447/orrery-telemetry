# fresh MCPの実効権限フラグを真偽値として照合する（fork段階31）

専用LaunchAgentのPIDとMCP応答を照合する `fresh_process_probe.validate_effective_status` は、従来 `adapter` の3フラグをPythonの辞書等価比較だけで判定していた。Pythonでは `0 == False` と `1 == True` が成立するため、実際には数値 `0` / `1` を返す別実装が、`read_only=false`・`spawn_enabled_on_server=false`・`message_enabled_on_server=true` という権限契約を満たすものとして認証され得た。

本段階では `read_only`、`spawn_enabled_on_server`、`message_enabled_on_server` がすべて実際の `bool` 型であることを先に要求し、その後に従来どおり3値とキー集合を照合する。数値フラグは `EFFECTIVE_AUTHORITY_UNVERIFIED` に収束させる。secret・レスポンス本文を出力せず、送信・再送・agent操作・LaunchAgent起動停止は行わない。

Mac Python 3.13の合成loopbackで、3フラグに整数を与えた3ケースが修正前に拒否されず失敗することを確認した。修正後は整数・浮動小数点それぞれの計6ケースを拒否し、probeテスト27件が成功した。ローカル検証の結果はPRに追記し、GitHub CIと独立レビューを別途確認する。専用Tunnel経由のChatGPT実接続、実エージェントの処理開始・完了、本番identity/allowlistの承認はこの変更では実証していない。

ロールバックは本段階のコード、テスト、日英文書、changelog fragmentのrevert。既定OFF、本番MCP/Mail/Tunnel、稼働agent、fork master、upstreamは変更しない。Dashboard公開APIは変更しないためAPI generationは据え置く。

対象テスト: `python3 -m unittest integrations.chatgpt_mcp.tests.test_fresh_process_probe -q`。
