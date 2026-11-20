### 隔離fresh MCPの実LaunchAgent再起動後も受付記録と冪等性を検証

Mac実機の専用fixtureで停止後の旧PID消滅を確認してから一度だけ同じLaunchAgentを再起動する受入試験を追加。実SDKで保存済み受付記録とMail instanceを読み直し、同一commitで追加配送されないことと終了時に両PIDが消滅することを確認する。[試験の範囲](../docs/mcp-fresh-launchd-restart-receipt.md)。
