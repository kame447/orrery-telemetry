### fresh MCPの受付記録モジュールを配布検証の必須一覧へ追加

forkのfresh送信受付記録モジュール `fresh_message_receipts.py` をMail配布物の固定モジュール一覧とfresh導入テストの一覧に登録した。欠落や意図しないモジュール追加を拒否する検査は維持し、パッケージ検査が失敗していた状態を修正する。既存の本番サービス設定、送信権限、Mailやagentを変更する機能は追加しない。検証方法は [受付記録の仕様](../docs/fresh-message-receipts.md) を参照。
