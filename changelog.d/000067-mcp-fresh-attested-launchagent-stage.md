### fresh MCPの検証済み起動コマンドを非起動LaunchAgent設定へ接続

固定release・Mail・第三者依存を検証する隔離Python起動コマンドを、専用LaunchAgentの新規plistへ結び付ける準備APIを追加しました。承認digestの再照合、既存ファイルを置き換えない発行、起動状態不明時の拒否を行います。RunAtLoadとKeepAliveは無効で、現段階は起動・常駐・本番切替をしません。Dashboard API世代は変更しません。詳細は[実行コマンドのLaunchAgent準備](../docs/mcp-fresh-attested-stage.md)を参照してください。
