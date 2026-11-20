### 隔離fresh MCPをMac実LaunchAgentから起動してSDK・Mail受理まで検証

実Macの一意な非自動起動LaunchAgentにfresh MCP本体を登録し、実PID、外部SDKによる6ツール発見・preview/commit/status、架空agent間のMail受理と冪等性を一度の隔離E2Eで検証できるようにした。試験用labelのbootout・旧PID消滅と秘密非露出も確認し、実Tunnel・agent受信・本番切替は対象外とする。[実接続試験](../docs/mcp-fresh-launchd-sdk-e2e.md)。
