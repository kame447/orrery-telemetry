### 登録 pending が無い場合の明示解消

利用者向けの登録解消で root または pending が無いときは、503 ではなく404 `TARGET_UNAVAILABLE` を返します。HTTP の認可、既存 file の検証、digest の一致確認は維持します。

### global dashboard の返信と指示の案内（API 17）

隔離 global mode の既存 Mail 欄に message ID による返信の流れ・一部省略・keyset 継続を表示します。古い指示 block は共通の検出結果を使い、起動時の候補観測と現在の disk 状態を分けて、日英 docs と片付け command のコピーへ案内します。operator 認可・数値の視点・固定 client slot を使い、folder と Mail の scope を分けます。成果物は明示 root のみです。既定の旧 mode と activation=false は維持します。

登録 pending の同 intent 再送を status の操作ボタンから実行し、表示時の digest を照合します。Lock は通信・遅延応答・tab 引継ぎを失効させ、storage が使えない場合は memory のみで Unlock できます。指示の確認は一致した slot だけを照合し、cleanup command は検出時の HOME と install を明示します。global 用 HTML は明示した shell と共通の style/manifest だけを読み込みます。

Lock 時に drawer の見出し・件数も消去します。画面で完結しない Codex trust と daemon の設定は CLI 手順へ案内し、不在の実行 profile は `PERSISTENT_EXECUTION_REQUIRED` で示します。
