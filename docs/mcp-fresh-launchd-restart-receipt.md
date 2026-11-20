# 隔離fresh MCPの実LaunchAgent再起動・受付記録維持（fork段階21）

段階20の実macOS LaunchAgent E2Eに、同じ専用fixtureの正常停止と一度だけの再起動を追加した。本番MCP、Mail、Tunnel、稼働agentは操作しない。

Macの対話セッションで環境変数 MCP_FRESH_LAUNCHD_E2E=1 を明示した場合のみ実行する。一意なlabel、一時HOME、専用Mail DB/instance、架空sender/recipient、専用state、動的loopbackポートを使う。RunAtLoadとKeepAliveはOFF。実SDKでpreview/commitのMail受理を確認した後、launchctl bootoutで登録を解除し旧PID消滅を検証する。問題がなければ同じfixture plistを一度だけbootstrap/kickstartする。

再起動後、実PIDが別物で旧PIDが残っていないこと、MCP SDKのstatusから同じMail instanceと永続receiptが読めること、同じpreview_id/digest/idempotency keyによる再commitでMail行が増えないことを検査する。終了時は試験用labelだけをbootoutし、両PIDの消滅と秘密・本文のログ非露出を確認する。判定不能時の自動再送・自動起動はしない。

実行：MCP_FRESH_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py

これは意図的な正常停止・再起動に限る。異常終了からの安全な復旧、固定releaseの実import照合、Tunnel/ChatGPT接続、agent受信・処理開始・完了、本番identity/allowlist再承認は未実証。execution_status=unobservedは処理開始を意味しない。

関連：[段階20の実接続](mcp-fresh-launchd-sdk-e2e.md)。
