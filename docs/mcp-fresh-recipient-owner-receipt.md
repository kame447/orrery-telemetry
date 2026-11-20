# 隔離fresh MCPの宛先本人によるMail受信・既読・ACK（fork段階22）

段階21の専用LaunchAgent・実MCP SDK・fresh Mailの隔離E2Eに、別の正規bindingを持つ架空recipientからの `FreshBoundClient` 受信を追加した。これはMCPのMail受理記録とは別の事象であり、実エージェントの稼働・通知配送・処理開始や作業完了を意味しない。

通常の隔離E2Eは、一時HOME・専用Mail DB/instance・架空sender/recipientを使用する。MCPのpreview/commit前に宛先のinboxが空であることを確認し、commit後に宛先の本人bindingで `fetch_inbox` を呼び、message ID・送信者ID・subject・本文が一致することを検査する。最初は `read_ts` / `ack_ts` が未設定である。送信者自身の資格情報による宛先の `mark_message_read` は `NOT_A_RECIPIENT` で拒否されることを確認したうえで、宛先の本人が `mark_message_read` と `acknowledge_message` を行い、それぞれの時刻を再読込して検証する。

Macの明示opt-in試験では、同じ宛先bindingで実LaunchAgentのMCP受理後に受信・既読・ACKし、正常停止→旧PID消滅→1回だけ再起動した後、改めて生成した正規clientから既読・ACK時刻が永続化されていることを確認する。MCP側 `orrery_message_status` は引き続き `execution_status=unobserved` として扱い、受信・既読・ACKを処理開始・作業完了に読み替えない。停止は専用labelのみで、Mail再送・自動復旧・本番サービス操作はしない。

通常テスト：`PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_message_e2e.py`

Mac実機テスト：`MCP_FRESH_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_launchd_server_e2e.py`

未検証：実エージェントの通知待機と処理開始・完了、異常終了の復旧、固定releaseの実行時import照合、専用Tunnel/ChatGPTとの接続、upstream受入と本番identity/allowlist再承認。本番MCP・Mail・Tunnel・稼働agentには触れない。

関連：[再起動後のreceipt](mcp-fresh-launchd-restart-receipt.md)、[SDKとMailの実受理](mcp-fresh-launchd-sdk-e2e.md)。
