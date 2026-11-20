# macOS CIのfresh MCP子プロセス待受診断（fork段階27）

PR #79でテスト用Pythonを専用venvに分離しても、fork PR #79〜#81のmacOS matrix（Python 3.11/3.12/3.14）では実子プロセス受入2件が `private fresh MCP did not listen` で失敗した。既存の20秒上限では、import/初期化の遅延とハングを区別できない。原因は未確定であり、venvの修正が成功したと扱わない。

この段階では本番のMCP処理を変更せず、`test_fresh_server_process_e2e.py` の隔離fixtureだけで待受期限を20秒から60秒へ拡張する。子が先に終了した場合はすぐに失敗し、60秒内にloopback待受を確認できない場合も失敗する。既存のfinallyでテスト用の子プロセスを終了し、本番MCP・Mail・Tunnel・agentは操作しない。

失敗メッセージには `child_running`、`startup_refused`、`listener_reported`、`log_nonempty` の真偽だけを含める。ログ本文、pairing URL・secret、登録token、送信本文は出力しない。秘密情報を含む合成ログで診断を検査する単体テストも追加する。この段階の変更で問題が解消するとは限らない。

確認の優先順は、まず新PRの対象テストがCIで通るか、次に全matrix・独立レビュー、最後に隔離Mac実機で再確認。GitHub CIが未完走または失敗ならDraftを維持する。60秒でも失敗した場合はboolean診断を用いて起動前の停止とプロセス終了を切り分け、別段階で最小修正を行う。固定release・実LaunchAgent・Tunnel・実agent受信・作業開始の検証完了を意味しない。

ロールバックは本PRのテスト変更、日英ガイドと索引、changelog fragmentをrevertする。Dashboard API generationは変更しない。
