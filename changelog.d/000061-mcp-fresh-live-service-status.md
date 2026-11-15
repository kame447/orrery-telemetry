### fresh MCPの固定LaunchAgent設定と実プロセスの送信権限を照合する

固定release・policy・launchd plistと、実PIDの接続先にあるMCPの公開ツール・送信権限をread-onlyで照合する部品を追加します。想定外の起動設定や別プロセスでは秘密を送る前に拒否します。常駐installer、実agent送受信、実Mac受入は別段階です。Dashboard API世代や本番環境は変更しません。詳細は[利用条件・検証限界](../docs/mcp-fresh-live-service-status.md)を参照してください。
