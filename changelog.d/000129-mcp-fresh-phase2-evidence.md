### fresh MCPの固定宛先と保存済み送信証拠を再検証

隔離試験用fresh MCPの宛先上限を16件にそろえ、超過時は起動・事前検査を拒否します。既存の宛先は削除・置換しません。保存台帳の本文・宛先・digest・request UUID・期限・受領記録について、操作開始時に見つかった不整合をMail操作前に拒否します。処理中に見つかった不整合では、送信済みの可能性を残して証拠を保全します。同じrequestの過去のfresh送信結果を返す場合も、実際の保存内容と受領記録の一致を確認し、不明な結果を成功や再送として扱いません。

[fresh送信の手順と制限](../integrations/chatgpt_mcp/FRESH_MESSAGES.md)に試験用instance限定と証拠保全を明記しました。既定OFF、正常な台帳・receipt形式、既存legacy運用を維持します。Dashboard API世代の変更はありません。
