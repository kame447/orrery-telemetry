# 固定releaseのfresh MCPと隔離受信者によるMail受信確認（fork段階34）

実macOS LaunchAgentで固定releaseのfresh MCPを起動する既存の明示opt-in試験を拡張する。既存の専用HOME・DB・Dashboard・pairing・policy・動的ポート・ランダムなLaunchAgentラベルだけを使用する。production gateway、Tunnel、Mail、稼働agentには接続しない。

実MCP SDKで6ツール、live PID/接続所有者、freshの送信権限、preview、正規Mail受理、永続receipt、冪等commitを確認した後、別の認証済みの試験用受信者（FreshBoundClient）が正規の `fetch_inbox` を呼び、受付message ID、sender ID、本文の一致と未既読・未ack状態を確認する。その受信者だけが `mark_message_read` と `acknowledge_message` を呼び、read/ackの記録を確かめる。送信側の `orrery_message_status` はこの後も `execution_status=unobserved` で変化しないことを検証する。Mail受理、認証済み試験クライアントによる取得、明示read/ackを切り分ける。

実測：2026-11-17、macOS Python 3.13、`MCP_FRESH_PINNED_LAUNCHD_E2E=1` で対象1件成功、依存パッケージ警告8件。実コマンド：

```sh
MCP_FRESH_PINNED_LAUNCHD_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:packages/agentstack_mail/src \
  /Users/Shogo/Developer/orrery-telemetry/.venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_fresh_pinned_launchd_e2e.py
```

試験は `finally` でテスト用LaunchAgentだけを停止し、PIDとポートの消滅と秘密・本文のログ非露出を検査する。これは試験用認証クライアントによる取得・明示ackであり、実エージェントが通知を受けた・処理を始めた・完了した証拠ではない。専用Tunnel→ChatGPTのfresh実接続も未検証。本番切替はupstream受入と本人によるinstance/allowlist確認が前提。全体CIと独立レビューも別途必要。ロールバックはこの試験追加・日英文書・索引とchangelog fragmentのrevertのみ。API generationは変更しない。
