### fresh専用MCP追加指示を独立した明示有効化入口で試せるようにする

既存forkのMCP・常駐機能と読み取り専用入口を維持し、固定したfresh clientと宛先IDを使うpreview/commit/statusを追加します。二重送信を避ける永続claimを保存し、結果不明を再送しません。本番切替や常駐化は行いません。Dashboard API世代は変更しません。詳細は[利用条件と制限](../integrations/chatgpt_mcp/FRESH_MESSAGES.md)を参照してください。
