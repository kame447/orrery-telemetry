### fresh MCPを起動する同一Python内で固定コードと依存を検証するコマンドを追加

既存の固定gateway・Mailと運用者承認済みの第三者依存を検証した後、将来の専用サービス管理者が使用する隔離Python起動引数を構築します。起動対象の子Pythonはimport前後に同じコード・依存を検査し、そのプロセス内でserveします。未承認の依存構成は拒否します。現段階では起動・常駐化・本番切替を行わず、既存設定は変更しません。Dashboard API世代は変更しません。詳細は[隔離importと同一プロセス実行の制限](../docs/mcp-fresh-import-attestation.md)を参照してください。
