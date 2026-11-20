### 隔離fresh MCPの起動を明示承認・実PID照合付きに限定

専用fixtureの固定plistを再検証し、運用者の起動用digestと未ロード・未使用portの条件を満たす場合に限り、launchctl bootstrapとkickstartを各1回実行するAPIを追加しました。起動後の実PIDと権限を照合できなければ成功と扱いません。結果不明時は自動再試行・停止・修復を行わず、既存サービスも操作しません。本番切替や停止・復旧、実Mac E2Eは未対応です。Dashboard API世代は変更しません。詳細は[隔離起動の契約](../docs/mcp-fresh-attested-start.md)を参照してください。
