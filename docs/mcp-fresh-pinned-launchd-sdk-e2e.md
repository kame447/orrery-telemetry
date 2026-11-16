# Mac実LaunchAgentで固定releaseのfresh MCPを受入検証（fork段階26）

前段階の固定release実MCP試験はPython子プロセスの起動までだった。本段階の `test_fresh_pinned_launchd_e2e.py` は、Macの対話ログイン環境で明示opt-inした場合にのみ、専用の一時HOME・Mail DB・Dashboard・pairing・policyと固定gateway/Mail/第三者依存のコピーを準備する。既定のDashboard 8770、本番MCP・Mail・Tunnel、稼働agentには接続しない。

`RunAtLoad=false`、`KeepAlive=false` のランダムな一時LaunchAgentを実 `launchctl bootstrap` と `kickstart` で起動する。plistの起動引数は `build_command` が生成した `python -I -S -B -c` であり、固定release、Mailソース、依存パッケージ、import元の検証を同じ常駐Pythonで行う。`launchctl print` のplistとPID、`lsof` が示す接続所有者を先に一致させ、その同一接続だけにpairing secretを送って、実RPCの6ツール・fresh Mail instance・sender/recipient・送信権限とspawn無効を確認する。続いて実MCP SDKでpreview全文、Mail受理、receipt、二重送信防止を検証し、処理開始は `unobserved` のままとする。

最後は必ず一時labelだけを `bootout` し、登録・旧PID・待受が消え、秘密や本文がログに露出していないことを確認する。登録/解除の結果が不明な場合は成功扱いせず、他サービスの停止や無条件再試行はしない。

```sh
MCP_FRESH_PINNED_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_pinned_launchd_e2e.py
```

この検証はテスト用LaunchAgentの起動・停止であり、本番固定releaseの導入やクラッシュ後自動復旧、Secure MCP Tunnel/ChatGPTの実接続、実agentの通知受信・処理開始・作業完了を意味しない。Mail受理をagent実行と混同せず、`unobserved` を未配送の証拠にしない。ロールバックは新しいテストと日英ドキュメント・索引・changelog fragmentの削除のみ。API generationは変更しない。
