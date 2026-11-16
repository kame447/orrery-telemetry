# fresh MCP応答後のLaunchAgent PID再照合（fork段階29）

LaunchAgentのPIDとTCP接続所有者を照合してからMCPへ接続しても、`orrery_status` 応答中にプロセスが終了・再起動する可能性がある。従来のprobeはMCP結果を検証した時点で返っており、最後の応答後に実PIDが変わっても「稼働中」と返し得た。

`fresh_process_probe.probe` は最後の `tools/call(orrery_status)` と実効権限の検証を終えた後、同じLaunchAgent labelと固定plistパスを `launchctl print` でもう一度検査し、開始時と同じPIDでなければ `SERVICE_PROCESS_CHANGED` として拒否する。取得不能やtimeoutは `SERVICE_PROCESS_UNVERIFIED` として拒否する。失敗理由は固定コードのみで、ペアリング情報・本文・API生応答を出さない。再起動・再送・サービス操作は一切しない。

検査結果はあくまで最後に観測した瞬間の証拠であり、その後もプロセスが稼働し続けることや実行中のimport元を証明しない。既存のpairing前の接続所有者検査と、同一ソケットを固定する制約は維持する。

合成loopback HTTP・launchctl/lsofテストに「MCP応答後のPID差替え」と「MCP応答後のlaunchctl timeout」を追加した。修正前は前者が検出されないことを赤テストで確認し、修正後は関連3ファイルで51 passed・30 subtests passed、文書・リンク検査を追加した全対象で134 passed・30 subtests passed。さらに専用隔離release/DB/動的ポートとMac実LaunchAgentの明示opt-in復旧試験が `1 passed, 5 warnings` で成功し、終了時に専用ジョブ・両PID・待受ポートの消滅と秘密のログ非露出を確認した。GitHub Actions全完走と独立レビューは未実施。本番MCP/Mail/Tunnel・稼働agent・fork master・upstreamには変更しない。

試験: `PYTHONPATH=integrations/chatgpt_mcp/src:packages/agentstack_mail/src .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_process_probe.py integrations/chatgpt_mcp/tests/test_fresh_attested_status.py integrations/chatgpt_mcp/tests/test_fresh_service_status.py`（`.venv` は既存リポジトリの開発用）。戻す場合はこのPRの追加PID照合・テスト・日英文書・changelog fragmentをrevertする。API generationは変更しない。
