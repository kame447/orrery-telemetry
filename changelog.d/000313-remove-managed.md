### 旧 managed 経路の削除

- API 25: 旧 `attempt_id` による起動要求と `spawn-status` の attempt 照会を廃止しました。専用の managed controller と、usage 必須・通信先制限を含む旧 dsh/Hermes runtime 層を削除しました。DeepSeek と Local は標準の起動、名前による状態照会、native resume、共通の後片付けを使います。

- 標準のDeepSeek・Local catalogと起動が使うmanifest参照およびNode pathを、通常の更新で引き継ぐ設定として分類しました。既存の設定名と導入optionを維持し、operatorの明示値を保存値で上書きしません。
