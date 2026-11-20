# fresh MCPのJSON応答深度を固定制限（fork段階42）

fork Draft PR #93以降のmacOS Python 3.14 CIでは、`test_overdeep_json_is_reported_as_probe_refusal`が失敗した（2026-11-17、run 43988112355 / 43988192667）。従来の検証はPythonのJSONデコーダが深い入力で例外を出すことに依存していたが、言語処理系の版によって受理できる深さが変わる。

段階42では、MCPの`initialize`、`tools/list`、`tools/call`のJSON-RPC応答をデコードした後、辞書・配列を反復処理で走査し、入れ子構造を最大64階層に固定して超過を拒否する。statusの`structuredContent`と`content`の照合でも、後者のJSONテキストに同じ上限を適用する。正規の応答は維持し、秘密、送信権限、送信処理、実サービスの設定は変更しない。

Mac Python 3.13で80段の入れ子を3種類の応答へ混入させた修正前テストは3/3で誤受理した。修正後には拒否を確認した。既存の2万段テストはPythonの版に依存しない安全な失敗を期待する。Python 3.14のGitHub CIでの再確認と独立レビューは未実施であり、CI成功は主張しない。

前提はfork Draft PR #97。revertすると固定深度制限がなくなり、従来どおり処理系側の解析制限に依存する。専用ChatGPT fresh Tunnel、実agentの受信・処理開始・完了、本番identity/allowlist承認、upstream受入は未達。fork master、本番MCP/Tunnel/Mail/稼働agentには触れない。
