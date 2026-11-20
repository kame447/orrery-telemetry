# fresh MCPのDashboard接続先を隔離して固定する（fork段階24）

従来の固定release用 `Plan` とplistは、Dashboard接続先を `http://127.0.0.1:8770` に固定していた。そのまま隔離実験を実行すると、MCPとMailを別プロセスにしても本番Dashboardへ接続し得る。特に送信権限付きfreshプロセスでは隔離の前提を満たさない。

`orrery_chatgpt_mcp.fresh_release_plan.Plan` に `dashboard_url` を追加した。既存のplanと完全互換の既定値は `http://127.0.0.1:8770` であり、既存サービスの設定は書き換えない。隔離試験のplanには `http://127.0.0.1:<専用のDashboardポート>` を明示指定する。指定可能なURLは、厳密なIPv4 loopback `127.0.0.1`、HTTP、1024〜65535のポート、パス・クエリ・認証情報・末尾スラッシュなしに限る。MCP自身の待受ポートとの同一指定も拒否する。さらに `fresh_attested_start.start(..., fixture_mode=True)` は既定8770のまま起動することを拒否し、隔離専用Dashboardの明示指定を要求する。ホスト名、外部IP、HTTPSや相対URLへのフォールバックは行わない。

固定releaseの `fresh_runtime_command.build_command` と `fresh_attested_stage`、旧非固定の `fresh_service_stage` と `fresh_service_status` の全てが、このplanの値を使用する。LaunchAgentのenvironment完全一致検証とpreviewの承認digestにも反映されるため、承認後に別Dashboardへ切り替えたplistは認証前に拒否される。plan指定だけで隔離が証明されるわけではなく、実際のDashboard/Mail instance、プロセスPID、source、送信権限の照合は引き続き必要である。

ロールバックは本PRのplanフィールド・参照先・テスト・ドキュメントを戻す。既存本番・fork master・upstream・既存Tunnel・Mail・agent・LaunchAgentには触れない。対象テストは `test_fresh_release_plan.py`、`test_fresh_runtime_command.py`、`test_fresh_attested_stage.py`、`test_fresh_service_stage.py`、`test_fresh_service_status.py`。実際の固定releaseからの起動・専用Tunnel接続・agent処理開始はこのPRでは実施していない。
