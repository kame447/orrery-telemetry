### fresh MCPのPID照合をOS標準launchctlに固定

pairing secretのHTTP送信前に読み取るLaunchAgentのplistとPIDを、PATH上の同名コマンドではなく`/bin/launchctl print`で照合。プロセス所有者の偽装を防ぐ回帰試験を追加。詳細は[システムlaunchctl固定](../docs/mcp-fresh-system-launchctl.md)。本番切替やagent処理の検証は対象外。
