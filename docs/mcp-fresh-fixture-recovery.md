# 隔離fresh MCPの不明結果を読み取りで確認する（fork段階15、Draft）

対象は段階13/14の専用fixture LaunchAgentです。起動・停止の呼び出し後に結果が不明でも、自動再試行、bootout、bootstrap、kill、restart、Mail再送を行わず、運用者の読み取り照合だけを実施します。本番サービス用の回復操作ではありません。

`fresh_fixture_recovery.inspect(plan, dependency, home, ..., fixture_mode=True, previous_pid=...)` はoperator-onlyのPython APIです。公開MCPツールやDashboard APIには追加しません。利用者が信頼できるlaunchd照会（`is_loaded` / `loaded_plist_path`）、既存の固定release/依存検査、実PID・MCP権限probeを渡す必要があります。専用HOME・Mail DB・instance・架空agentの分離は別途運用者が実証してください。

ロード中の場合は、plistの完全一致、固定ソース、実PID、権限を再検証できたときだけ `running_attested` を返します。以前のPIDが異なれば `same_as_previous_pid=false` とし、同じプロセスであるとは見なしません。ジョブ未ロードの場合は `job_unloaded_observed` とし、旧PIDが提示された場合だけ存在確認を2回行います。旧PIDが残る・照会不能・途中状態変更・異なるplist・不正な権限の場合は固定エラーで拒否します。PID不在やlaunchd未ロードは「安全に再起動できる」「Mailが未送信」の証明ではありません。

すべての正常な応答で `restart_authorized=false`、`stop_authorized=false`、`service_recovered=false` を返します。Mail受理、通知配送、agent受信、処理開始、作業完了を推定しません。次の手順は専用fixtureの状態と前回結果を手動照合し、新しいpreview/承認を得たうえで別の明示的操作を行うことです。未知の送信結果には、正規のread-only receipt照合だけを使います。

## 検証範囲

隔離Python 3.11以降で新規15件のunittestを通常実行と `python -O` で実行しました。新しいモジュール自体は実行していますが、既存のlaunchd、PID、probe依存をスタブ化した合成検証です。MacのDesktop Commanderは初回接続に失敗し、その後の読み取り4回もSession terminatedとなったため、実リポジトリvenv、実macOS/launchd、MCP SDK/Tunnel、Mail/agent E2Eは未検証です。CIと第三者レビューの完了も別途確認が必要です。

本番のlegacy MCP/Tunnel/Mail/稼働agent、fork master、upstreamの状態は変更しません。段階15をmergeしなくても既存運用は変わりません。
