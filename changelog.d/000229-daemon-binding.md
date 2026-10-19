### 常駐 daemon が Mail の識別子を確認し、共通の通知待機を使えます

同梱 Mail の `health_check` に、管理 socket と同じ永続的な `server_instance_id` を追加します。秘密ではない UUID で、別の Mail への誤接続を拒否できます。利用する側が必要な field を判定できるよう、互換世代を11から12に上げました。API 11の watcher health と古い signal の扱いは保ちます。

`agentstack-await-reply` の認証済み fetch を渡せる待機処理を共通化し、取消しに対応しました。常駐 daemon 用の厳密な binding profile と operator 専用 CLI は、同じ owner・数値ID・project・固定送信先を照合します。待機は本文取得や ACK を行いません。CLI は固定 source から実行する対象で、一般の installer には追加しません。identity 作成と実際の配備は operator が別途確認します。
