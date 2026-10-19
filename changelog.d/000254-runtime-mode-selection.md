### mode の選択を client・dashboard・service で共通化

- mode の選択を owner の認証から分離し、context のない operator/service が global 選択後に旧 Mail へ戻らないようにしました。既定の legacy と activation=false は維持します。
- 新規 process は既存の設定から mode を一度選び、稼働中の hook・worker は継承します。明示 mode と context/config の食い違いは理由付きで拒否します。
- context のない watcher は既存の route ごとに共通配送処理を使い、不正な宛先を分離します。詳しくは [global delivery client](../docs/global-delivery-client.md) を参照してください。
- system controller の installed default と稼働中の hook の継承を分け、App/直接 bootstrap の partial 配置・設定競合を共通入口で拒否します。一時的な watcher の通信断は再試行します。
- 新しい pane へ mode を渡し、直接 bootstrap の project 設定を保持します。旧 helper の部分配置と watcher の明示 server config も共通選択へ揃えます。
- 起動と子登録の bootstrap に明示 context を引き継ぎ、既定の旧 mode との誤った衝突を防ぎます。
- 未固定の継続で配置済みの既定を確認できない場合は停止し、古い helper から旧登録へ戻ることを防ぎます。説明文中の `--literal` は installer の指定と区別します。

- global service の起動専用の環境は子 process へ直接渡し、起動に失敗しても呼出し元の環境へ設定を残しません。
