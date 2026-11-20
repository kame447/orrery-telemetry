# 固定releaseから実MCPを起動する隔離受入試験（fork段階25）

従来のMac実LaunchAgent受入試験は、開発用 `PYTHONPATH` と `python -m orrery_chatgpt_mcp.fresh_server` を使用していたため、固定releaseのコード・Mail client・第三者依存を同一プロセスで検査してからMCPとして応答できることまでは確認できなかった。

`test_fresh_pinned_runtime_e2e.py` は、テスト用ディレクトリにgateway固定release、Mailソースの専用コピー、現在の開発venvから第三者依存の専用コピーを用意する。`.pth`・bytecode・cacheを含めず権限を読み取り専用に固定し、承認manifestでバンドルを照合する。`build_command` が作る `python -I -S -B -c` の実コマンドを実行し、子プロセスが同じinterpreter内で固定sourceとimport元を二度検査してから `serve` に進めることを受入する。Mailは一時DBと架空の送受信agent、Dashboardは専用127.0.0.1動的ポート、pairing/policy/stateも専用。既定の8770には接続しない。

実MCP SDKで6ツールの発見、preview全文、正規Mail受理、receipt、再commit時の二重送信防止、`execution_status=unobserved` を検証する。終了後に子プロセスを必ず停止し、MCPログに秘密・本文が無いことを確認する。その後、テスト用固定releaseの一ファイルを改変し、同じ起動コマンドがexit 78で拒否され、待受を開始しないことも確認する。

重いバンドルコピーが必要なため、通常CIではskip。Macの既存開発venvで次を明示して実行する。

```sh
MCP_FRESH_PINNED_RUNTIME_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_pinned_runtime_e2e.py
```

これは本番releaseの更新、launchd常駐実行、Secure MCP Tunnel接続、実稼働agentの通知受信・処理開始・作業完了を証明しない。送信受理と作業開始は別段階であり、`unobserved` は未受信や未送信を保証しない。既存MCP・Mail・Tunnel・agent・fork master・upstreamは変更しない。ロールバックは本テスト、日英文書・索引とchangelogの削除のみ。
