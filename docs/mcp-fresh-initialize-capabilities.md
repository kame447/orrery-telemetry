# fresh MCP初期化時の公開capabilitiesを厳密に照合（fork段階39）

実プロセス検証では従来、MCPの`initialize`応答で`capabilities.tools`が辞書であることだけを確認していました。この条件では、同じ6件の`tools/list`を返していても、サーバーが`resources`・`prompts`・`logging`・`experimental`などの追加機能を公開し、固定されたMCP契約を変えている可能性を見逃します。`tools.listChanged=true`は後続のツール一覧の変化も示唆します。

fresh専用の隔離probeでは、既存forkサーバーの実際の`initialize`契約である`capabilities={"tools":{"listChanged":false}}`だけを受理します。キーの欠落、追加capability、`listChanged`の真偽値型の違いもすべてfail-closedで拒否します。表示用の`serverInfo.title`やバージョン文字列は権限とは別であり固定しません。通常のMCP本体や本番サーバーのcapabilitiesは変更しません。

隔離loopbackで、変更前には7ケースが誤受理されることを再現しました。変更後は同7ケースを拒否し、正常なMCP初期化・6ツール発見・状態照合を維持します。秘密情報は出力しません。ここで証明できるのは起動中プロセスの応答内容までで、専用Tunnel経由のChatGPT接続や実エージェントの処理開始・完了の保証ではありません。

fork Draft #94を前提とし、実運用のGateway、Mail、Tunnel、稼働agent、fork master、upstreamは変更しません。無効化にはこの段階の変更をrevertできますが、追加capabilityを検出できない旧動作に戻ります。upstream受入と本番identity/allowlistの明示確認までは本番切替を行いません。
