# fresh MCPの専用LaunchAgent設定を安全に準備する（fork段階7）

この段階では、運用者が承認した内容で専用LaunchAgentのplistを新規作成するところまでを実装します。launchctlへの登録、プロセス起動・停止、再起動、既存サービスの上書き、fresh本番への切替は行いません。PR #61の実プロセス照合と別の段階です。

`orrery_chatgpt_mcp.fresh_service_stage.preview(plan, home, *, verify_release, read_release)` はPR #60の静的preflightを呼び、Planに固定したrelease/policy/送信者・宛先・instanceを検査してから、PR #61のlaunchd設定の完全一致契約に従ってplistを組み立てます。結果は、設定そのものを公開せず、stageの承認用digest、service label、port、plist名、release digestだけを返します。previewはファイルを書かず、launchctlも呼びません。

`stage(plan, home, approval_digest, *, verify_release, read_release, is_loaded)` は同じpreflightと設定生成を再実行し、承認したdigestが同一であることを確認します。このdigestはplistだけでなく、source commit、release digest、Mail instance、sender ID、recipient ID・期待名称にも紐付けています。設定の変更には新しいpreviewが必要です。`is_loaded` は信頼済みhost側の読取り用関数で、正確にFalseと確認できた場合に限り先へ進めます。確認不能、例外、既存のlabel、既存plistを「未起動」と推測して書き換えることはありません。

出力先は運用者が指定した正規のhome直下の `Library/LaunchAgents/<専用label>.plist` のみです。homeとLibrary/LaunchAgentsまでの実directoryを確認し、symlink、別owner、group/worldの書込み許可を拒否します。tempfileへ書込・fsyncした後、同directoryで `os.link` により一度だけ新しいファイルを公開するため、競合する既存ファイルを置き換えません。plistは0644、`Umask=0077`、`RunAtLoad=true`、`KeepAlive=true`で、正規のfresh entrypointと固定した9個の環境変数以外を設定しません。

このAPIはPythonを操作する信頼済み運用者向けです。MCP toolにも、通常のlegacy gatewayにも追加しません。`verify_release`・`read_release`・`is_loaded` は本番環境では既存の `service` の正規実装に固定する必要があります。テスト用スタブを実運用の証明として使うことはできません。stageは起動操作をしないため、ポートの未使用、Python依存とimport元、Mail client実到達性、実プロセスの権限・配送・エージェント実行を証明しません。静的preflightでも `dependencies_verified=false` のままです。

成功応答は `operation=staged_inactive`、`process_started=false`、`launchd_loaded=false` と明示します。失敗時は、書込後のfsync等で結果不明となる可能性があるため、既存ファイルや実行中プロセスを自動削除・再起動しません。作成先を直接確認し、以降の手順は次段階の安全な起動・停止・復旧CLIが完成するまで手動でも実行しないでください。独立したlabel/port、freshへの切替条件と固定IDの本人承認は引き続き必要です。

検証は一時home/LaunchAgentsと固定値だけを使い、プレビュー・新規作成・変更された承認・別宛先・release差替え・既存ファイル・symlink・競合書込・launchctl状態不明・不安全なdirectoryを対象にしています。launchctlのbootstrap/bootoutや本番Mail送信は行いません。既存legacyのMCPサービス、Tunnel、Mail、エージェント、fork master/upstreamには触れません。
