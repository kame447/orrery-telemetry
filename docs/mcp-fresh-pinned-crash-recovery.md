# 固定release fresh MCPの異常終了後の明示復旧（fork段階28）

段階26ではMacの専用LaunchAgentを正常停止できることを検証したが、稼働中プロセスの異常終了と再起動後の受付記録は未検証だった。本段階は、本番サービスを操作せず、独立した固定release・専用第三者依存・Mail DB/instance・架空agent・動的loopbackポート・非自動起動のLaunchAgentを使った、明示opt-inの受入試験を追加する。

起動したジョブのlabel/plist/PIDに加え、MCP実接続のTCP所有者と実効のinstance・送信者・recipient許可・spawn無効を読み取り専用で照合した後、その検証済みfixture PIDだけにSIGKILLを送る。 `KeepAlive=false` のまま自動再起動しないことを確認し、同じfixture labelを `kickstart` で明示的に再開する。復帰後は新PIDの接続所有者と権限を再照合し、実MCP SDKで保存されたreceiptを照合して、同じidempotency keyのcommitがMailに重複書込みされないことを検証する。異常終了の直前にMailが受理したことと、agentが作業したことは別である。

Mac実機（Python 3.13）の試験では `1 passed, 5 warnings`。試験後の専用LaunchAgent登録、旧/新PID、待受の消滅と、ログへのpairing secret/本文/token非露出を確認した。これは検証済みのfixtureを一度停止して明示再開する操作の試験であり、未確定のsend結果への自動再送・未知結果の自動復旧や、実agentの処理開始・完了を保証するものではない。さらに、専用Secure MCP Tunnel・ChatGPT実接続、本番へのidentity/allowlist承認、upstream受入と全CI/独立レビューは未達である。

実行例（利用者が明示的に許可した隔離テスト専用、Mac GUI sessionが必要）:

```sh
MCP_FRESH_PINNED_CRASH_E2E=1 PYTHONPATH=.:integrations/chatgpt_mcp/src:integrations/chatgpt_mcp/tests:packages/agentstack_mail/src \
  .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_pinned_crash_recovery_e2e.py
```

既定ではskip。戻す場合は本PRのopt-inテスト、日英ガイド/索引、changelog fragmentをrevertする。API generationの変更はない。
