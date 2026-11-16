# Mac実LaunchAgentでの隔離fresh MCP SDK・Mail受入（fork段階20）

段階18の「無通信Pythonをlaunchctl起動する」試験と、段階19の「実fresh MCPをPythonサブプロセスからSDK接続する」試験を、同一の専用LaunchAgentで結合する。これは本番常駐サービスや固定releaseの切替ではない。

`integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py` はmacOSの対話ログインセッションで `MCP_FRESH_LAUNCHD_E2E=1` を明示した場合だけ起動する。リポジトリの開発用venvとテスト用PYTHONPATHを使い、専用一時HOME・Mail DB/instance・架空sender/recipient・policy・MCP state・Dashboard・空きloopback port・一意な `org.agentstack.chatgpt-mcp-fresh.fixture-e2e-...` labelを用意する。実Mailもfixture専用であり、本番Mailとは接続しない。

plistは0600、`RunAtLoad=false`、`KeepAlive=false`。既存labelが存在しないことを確かめ、`launchctl bootstrap` → 登録済みplist/非稼働状態確認 → `kickstart` → 実PIDとMCPポート確認の順に実行する。FastMCPの外部SDKクライアントから6ツール発見、statusのMail instance・送信許可、preview全文、commitによるMail受理、statusで同一receipt確認、同一idempotency keyの重複受理防止を検証する。終了時は試験用labelだけを`bootout`し、登録解除と旧PIDの消滅、ログへの秘密・本文非露出を確認する。途中失敗時もfinallyで停止を試み、停止結果が不明なら成功扱いにしない。

実行例（既存Macの開発環境、独立worktreeから）：

```sh
MCP_FRESH_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py
```

この試験で実証するのは隔離LaunchAgent下のMCPプロセスと試験専用Mailへの「受理」まで。通知配送、実agentの受信・処理開始・完了、Secure MCP Tunnel/ChatGPTからの実接続、固定済みreleaseの実import元照合、異常終了後の安全な再起動・復旧、upstream受入、本番identity/allowlist再承認は未実証。受理時の`execution_status=unobserved`を処理開始・完了に読み替えない。既存の本番MCPやlegacyの許可設定には触れない。

関連：[段階18のOSライフサイクル](mcp-fresh-launchd-smoke.md)、[段階19の別プロセス受入](mcp-fresh-process-sdk-mail-e2e.md)。
