### 隔離 dashboard の読取と登録の共通入口

API 世代15に、私有 operator capability の HTTP 入場条件と管理 socket の ui_read を追加しました。公開 Mail tool は増やさず、可視集合・MAX・keyset・revision を同じ読取 transaction で確認し、dashboard は global の DB を開きません。登録と子登録は同じ pending・同じ token の再送と finalize を共用し、固定 root の状態・同名の既存 root を読取だけで確認できます。既定は旧 mode・activation=false のままで、画面と cockpit の接続は後続です。

名前の重複は登録前に拒否し、異常な root は個別に診断します。provider の指示が未設定なら設定待ちの状態を返し、登録競合の明示解消は同じ pending と digest の照合を HTTP でも利用できます。operator capability の CLI は隔離 HOME の明示を必須にしました。
