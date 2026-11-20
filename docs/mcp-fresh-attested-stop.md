# 隔離fresh MCPの承認付き停止（fork段階14、Draft）

前段の承認付き起動に対する、運用者専用の停止候補API。公開MCPツールではなく、本番環境・既存agent・Mail/Tunnelの停止処理に接続しない。実Macでのlaunchd bootoutはまだ実行していない。

`fresh_attested_stop.preview` は専用fixture label、固定release、private plist、実際にロードされたplist path、実PID、MCPの有効な6ツールとメッセージ権限を読み取りで照合する。検証した固定設定とPIDを、起動とは異なる領域の停止承認digestに結び付ける。未稼働または結果不明のサービスは止めない。起動結果が不明なジョブの自動回復経路ではない。

`stop` は `fixture_mode=True`、停止用digest、同一plist・PIDの再照合、PIDが存在することを確認したときにのみ `/bin/launchctl bootout gui/<uid>/<fixture-label>` を一度だけ実行する。通常の本番labelと、`fixtureevil` のような紛らわしいprefixは拒否する。bootoutの結果が成功でも、専用ジョブのアンロードと直前の実PIDの消滅を確認できた場合だけ `fresh_stop_verified` を返す。

応答不明・timeout・失敗・再検証の不一致があれば `FRESH_STOP_OUTCOME_UNKNOWN` を返し、成功・失敗・未停止のいずれとも断言しない。自動再試行・kill・restart・別labelの修復をしない。PIDの存在検査にはシグナル0しか使わず、他のプロセスを停止しない。起動時から切替までの競合は完全には排除できないため、意図しない対象への操作を防ぐにはoperatorが専用fixtureを他用途から隔離する必要がある。

起動後・停止後のコードimport provenance、Mail送信、エージェントによる受信・処理開始は別の観測事項。今回のユニットテストではlaunchctl、PID、MCP照合を模擬し、実launchd・実通信・停止のMac E2Eは未実施。再起動・結果不明ジョブの復旧も未対応。upstreamがfresh本番切替を受け入れ、利用者が新instanceのidentity/allowlistを明示確認するまでは本番に導入しない。

前提は[承認付き起動](mcp-fresh-attested-start.md)と[読取り専用の実PID照合](mcp-fresh-attested-status.md)。
