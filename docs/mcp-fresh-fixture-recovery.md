# 隔離fresh MCPの不明結果を読み取りで確認する（fork段階15、Draft）

対象は段階13/14の専用fixture LaunchAgentです。起動・停止の呼び出し後に結果が不明でも、自動再試行、bootout、bootstrap、kill、restart、Mail再送を行わず、運用者の読み取り照合だけを実施します。本番サービス用の回復操作ではありません。

`fresh_fixture_recovery.inspect(plan, dependency, home, ..., fixture_mode=True, previous_pid=...)` はoperator-onlyのPython APIです。公開MCPツールやDashboard APIには追加しません。利用者が信頼できるlaunchd照会（`is_loaded` / `loaded_plist_path`）、既存の固定release/依存検査、実PID・MCP権限probeを渡す必要があります。専用HOME・Mail DB・instance・架空agentの分離は別途運用者が実証してください。

ロード中の場合は、plistの完全一致、固定ソース、実PID、権限を検証し、読み取りの終盤でも同じPIDと権限を再probeしてから `running_attested` を返します。同じlabelでも途中でPIDが交代した場合、権限が検証不能になった場合、plistやlaunchd登録状態が変わった場合は拒否します。二重probeは観測途中の変化を検出するためのもので、観測後まで継続稼働を保証するものではありません。以前のPIDが異なれば `same_as_previous_pid=false` とし、さらに旧PIDの不存在を前後2回確認します。旧PIDが生存・不明・途中で再出現した場合は、二重稼働を見逃さないよう `running_attested` を返さず拒否します。不存在を確認できた場合だけ `previous_pid_absent_observed=true` を返します。PID番号の一致や瞬間的な不存在はプロセスの連続同一性・将来の安全性の証明ではありません。ジョブ未ロードの場合は `job_unloaded_observed` とし、旧PIDが提示された場合だけ存在確認を2回行います。旧PIDが残る・照会不能・途中状態変更・異なるplist・不正な権限の場合は固定エラーで拒否します。PID不在やlaunchd未ロードは「安全に再起動できる」「Mailが未送信」の証明ではありません。

すべての正常な応答で `restart_authorized=false`、`stop_authorized=false`、`service_recovered=false` を返します。Mail受理、通知配送、agent受信、処理開始、作業完了を推定しません。次の手順は専用fixtureの状態と前回結果を手動照合し、新しいpreview/承認を得たうえで別の明示的操作を行うことです。未知の送信結果には、正規のread-only receipt照合だけを使います。

## 段階16：旧PIDが残る場合の拒否

段階15の読み取り照合では、新たに確認した実PIDが以前のPIDと異なっていても、旧PIDがまだ稼働しているかどうかを検査せず `running_attested` を返せました。段階16ではこの場合に旧PIDの不存在を二度確認し、残存・権限不明・途中の再出現を固定エラーで拒否します。Mailの再送やlaunchd操作は一切行いません。新規3件の回帰テストと既存1件の期待値更新を追加しましたが、Mac実機・実リポジトリvenv・CIでの結果は未確認です。この検査だけで専用HOME・DB・Mail instanceの隔離やagent受信を実証したことにはなりません。

## 検証範囲

初版の新規15件のunittestは隔離Pythonで通常実行と `python -O` の両方で成功しました。今回の二重probeに関する追加4件はGitHubへpush済みですが、Mac接続不能とCI待機のため未実行です。新しいモジュール自体は実行していますが、既存のlaunchd、PID、probe依存をスタブ化した合成検証です。MacのDesktop Commanderは初回接続に失敗し、その後の読み取り4回もSession terminatedとなったため、実リポジトリvenv、実macOS/launchd、MCP SDK/Tunnel、Mail/agent E2Eは未検証です。CIと第三者レビューの完了も別途確認が必要です。

本番のlegacy MCP/Tunnel/Mail/稼働agent、fork master、upstreamの状態は変更しません。段階15をmergeしなくても既存運用は変わりません。
