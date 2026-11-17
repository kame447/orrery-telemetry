### fresh Mailの送信結果を再送せず本人の受付記録から確認する

protected clientのreceiptとget_message_receiptを追加し、本人のinstance・資格と保存済み本文/宛先の整合性を読み取りtransactionで検証します。未観測は再送許可と扱わず、本文や資格は返しません。Dashboard API世代や既存サービスは変更しません。[利用条件](../docs/fresh-message-receipts.md)。
