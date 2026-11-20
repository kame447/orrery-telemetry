# 隔離fresh MCPの実プロセス・SDK・Mail受入（fork段階19）

従来のin-process Gateway検証と無通信launchctl fixtureに加え、`fresh_server serve` を別Pythonプロセスで実行する自動テストを設けた。`integrations/chatgpt_mcp/tests/test_fresh_server_process_e2e.py` は一時HOME、専用fresh Mail DB・instance、2名の架空agent、専用sender context、0600のpolicyとpairing state、127.0.0.1の動的ポート、独立Dashboard HTTP fixtureを利用する。実運用のDB、トークン、ポート、agent、LaunchAgent、Tunnelには接続しない。

MCP標準SDK互換クライアントの別プロセスHTTPセッションで6ツールの発見、instance照合、preview全文、commit受理、永続receipt status、同一keyでの再commit無重複を検査する。専用DBはテストfixtureに限って読み取り専用で照合する。別のケースではpreview後にfixture Dashboardのinstanceだけを入れ替え、commitを拒否しDBに書かないことを検証する。fixtureでの受理はagentによる受信や処理開始・完了を意味しない。

テスト用fresh Mailプロセスは既存のfixtureが終了保証を持つ。追加したfresh MCPプロセスは `finally` で停止し、ログ内にpairing secretや本文を含まないことを検査する。サービス自身の固定release検証・launchd常駐起動・実Tunnel/ChatGPT接続・本番切替は対象外であり、追加承認を代替しない。

実行例：

```sh
PYTHONPATH=integrations/chatgpt_mcp/src:packages/agentstack_mail/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_server_process_e2e.py
```
