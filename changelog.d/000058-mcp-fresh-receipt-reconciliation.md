### MCPの不明な送信結果を再送せず本人の受付記録から照合する

unknown/sendingの状態を正規clientのreceipt読取りで照合し、確認済みの受理・拒否だけを保存します。未観測や照会不能はunknownのままで再送しません。preview本文との一致と同時完了時の状態後退を検査します。Dashboard API世代と本番サービスは変更しません。[制限と運用](../integrations/chatgpt_mcp/FRESH_MESSAGES.md)。
