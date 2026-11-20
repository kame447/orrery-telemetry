### 隔離fresh MCPの宛先本人による受信・既読・ACKを再起動前後で検証

実MCP SDKからのMail受理と、別資格情報の架空recipientによる `fetch_inbox` / `mark_message_read` / `acknowledge_message` を分離して検証。送信者による宛先の既読操作は拒否し、専用Mac LaunchAgent正常再起動後も既読・ACK時刻が維持されることを確認する。実エージェントの処理開始・完了を保証しない。[検証範囲](../docs/mcp-fresh-recipient-owner-receipt.md)。
