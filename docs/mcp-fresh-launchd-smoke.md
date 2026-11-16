# macOS実launchdの隔離fixtureスモーク（fork段階18）

段階13〜16の起動・停止・再照合は主にモックを使用していた。段階18ではmacOSのログインセッションで本物の `launchctl bootstrap` / `print` / `kickstart` / `bootout` を一度ずつ使用して、隔離したLaunchAgentの起動・PID観測・解除を確認するテストを追加した。

`integrations/chatgpt_mcp/tests/test_fresh_launchctl_macos.py` はmacOSかつ環境変数 `MCP_LAUNCHD_E2E=1` の場合だけ実行する。通常のCIとLinux/Windowsではskipし、Macでも明示opt-inなしでは実行しない。

テストは `tmp_path/Library/LaunchAgents` にmode 0600のplistを作る。ランダムな `org.agentstack.chatgpt-mcp-fresh.fixture-smoke-...` labelに、90秒で自動終了する無通信のPython待機プロセスを設定し、`RunAtLoad=false`、`KeepAlive=false` とする。事前に同labelが存在しないことを確認し、bootstrap後は登録plistの実パス、LaunchAgent種別、未起動状態、kickstart後のrunning/PIDを照合する。最後に `finally` で対象labelにbootoutを一度だけ行い、ジョブ解除と旧PIDの消滅を確認する。既存の本番MCP、Mail、Tunnel、agentや他labelには触れず、ポートを開いたりMailを送信したりしない。

Mac実機でのスモーク成功は「OSのlaunchctl登録・起動・解除が専用fixtureで動作する」ことだけを意味する。fresh MCP実プロセス、固定releaseのimport元、MCP権限、DB/instanceの隔離、正規MCPクライアント、Tunnel、エージェント受信・処理開始を実証しない。これらは依然として独立した受入条件であり、本番fresh切替の承認ではない。

実行例（既存の開発用venvを使用し、production venvにはインストールしない）：

```sh
MCP_LAUNCHD_E2E=1 PYTHONPATH=integrations/chatgpt_mcp/src:packages/agentstack_mail/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_launchctl_macos.py
```

CIでは明示opt-inを与えずskipとする。異常終了時の結果を再試行・成功扱いせず、実ジョブの残存を確認すること。次の段階で実freshサービスの隔離E2Eを行う。
