### fresh MCP応答後にLaunchAgent PIDを再照合して停止・再起動を検出

最終MCP status応答の後で固定plistとPIDを再照合し、応答中にプロセスが終了・交代した場合やlaunchctlが判定不能の場合は、稼働確認を成功として返さない。失敗時は固定コードのみ返し、秘密・本文を露出させない。既存サービスを操作せず、合成検証51件と関連文書・リンクを含む134件、および隔離Mac LaunchAgent受入1件で確認した。API generationは変更しない。

詳細: [日本語](../docs/mcp-fresh-post-rpc-pid.md) / [English](../docs/mcp-fresh-post-rpc-pid.en.md)
