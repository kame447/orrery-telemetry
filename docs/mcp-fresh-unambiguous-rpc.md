# fresh MCP実プロセス検証のJSON応答を一意に解釈する（fork段階30）

専用LaunchAgentのPID・接続所有者を確認した後でも、実MCP応答を検証する際に重複JSONキーを許すと、異なるパーサーで解釈が分かれる。従来の json.loads は同じキーの最後の値を採用していた。例えば、同じ応答に "runtime_mode":"legacy" と "runtime_mode":"fresh" を並べても、後者だけで実効権限確認を通過し得た。また、Pythonでは True == 1 なので、JSON-RPCの "id":true をリクエストID 1 と誤って受理し得た。

fresh_process_probe._request では、JSONのすべての階層で同一オブジェクト内のキー重複を拒否する。JSON-RPCの id は int 型であることも必須にし、bool を排除する。いずれも固定エラーコード MCP_STATUS_UNVERIFIED に収束させ、生のレスポンス・本文・pairing secretを例外に含めない。正しい実MCP応答と、従来のプロセス所有者・固定ソケット・メッセージ認可検証は維持する。送信、再送、LaunchAgent操作はしない。

合成loopback MCPを使い、真偽値のID、同値重複ID、入れ子の実行モード重複の3ケースを追加した。修正前はいずれも拒否されないことを確認し、修正後に26件のprobeテストが成功した。CIの全完走・独立レビュー・専用Tunnelと実エージェントの受信・処理開始はこの修正の証拠ではない。適用しない場合はこの段階のコード・3テスト・日英文書・changelog fragmentをrevertできる。既定OFF、本番MCP/Mail/Tunnel、稼働エージェント、fork master、upstreamは変更しない。

対象テスト: python3 integrations/chatgpt_mcp/tests/test_fresh_process_probe.py -q。この変更はDashboard公開APIを変えないためAPI generationは変更しない。
