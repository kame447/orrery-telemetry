### 隔離したfresh MCP実プロセスでSDK・Mail受理と拒否を検証

実際の `fresh_server serve` を別プロセスで起動し、専用fresh Mail・架空agent・専用Dashboard・MCP SDKによる6ツール発見、preview/commit/status、二重送信防止、instance変更時の拒否を検査するテストを追加した。終了時に試験プロセスを必ず停止する。固定release/launchd常駐、Tunnel、agentの受信・処理開始、本番fresh切替の検証ではない。[詳細](../docs/mcp-fresh-process-sdk-mail-e2e.md)を参照。
