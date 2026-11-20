### 隔離fresh MCPの停止を実PID・明示承認付きに限定

固定plistと実PID・有効権限を照合した専用fixtureについて、停止用digestで明示承認された場合のみlaunchctl bootoutを一度実行する運用者APIを追加しました。実ジョブのアンロードと旧PID消滅を確認できなければ成功を報告せず、再試行・kill・自動復旧をしません。起動側のfixture labelの曖昧な前方一致も拒否します。公開MCPツールとDashboard API世代は変更せず、実Macの停止・再起動・本番切替は未実施です。[仕様](../docs/mcp-fresh-attested-stop.md)を参照してください。
