# fresh MCPのstatus本文二形式の一致検証（fork段階37）

MCPの`tools/call(orrery_status)`は、機械処理用の`structuredContent`に加え、クライアントが読む`content`内のJSONテキストも返します。以前の実プロセス検証は前者だけを照合していたため、後者が異なる実行モード・送信権限・認可先を表示していても成功と判定できました。MCP利用者に見える権限と検証器が判断した権限が不一致のまま進むことを防ぐ修正です。

専用の`fresh_process_probe`は、statusのテキスト要素が1つのJSONオブジェクトであり、`structuredContent`と型を含め同じ値を持つことを確認します。欠落、複数要素、JSON不正、重複キー、非有限数、真偽値と数値の食い違い、異なる権限は拒否します。出力には本文やpairing secretを含めず、既存の認可・起動停止・送信操作を拡張しません。

隔離loopbackの疑似MCPで、変更前に不一致6ケースが拒否されないことを再現し、変更後に拒否されることを確認します。実LaunchAgent・専用Tunnel/ChatGPT実接続・実エージェントの処理開始/完了・本番identity/allowlist承認・upstream受入は本修正の証明対象外です。本番の既存MCP/Mail/Tunnel/agentを変更しません。

無効化はこの段階の変更（本体・テスト・日英ガイド・索引・changelog）を一括でrevertしますが、不一致を見逃す旧判定への後退となります。前提はfork Draft PR #92です。
