### tmux の接続先と旧 consumer の対象保護を共有

- 固定 client root の既存 launch/live を読取だけで照合する共有 helper を追加しました。旧 consumer が global pane に作用する操作や、所属を確認できない対象への操作を理由つきで拒否できます。
- 対象の拒否と helper 自体の失敗を分け、配置の修復に使う読取診断を提供します。CLI の成功応答も共有 schema で検証します。
- 既定 legacy・activation=false を維持し、専用 global 起動はこの段階では有効にしません。公開 Mail tool と dashboard API の変更はありません。
- [共有境界の契約](../docs/runtime-tmux.md) と [English contract](../docs/runtime-tmux.en.md) を追加しました。
- 長時間動く consumer は共有コードを一度だけ読み込み、literal 設定の読取結果を file 属性が同じ間だけ再利用します。配置 snapshot は呼出しごとに読み直します。
- window 選択でも現在の全 pane を共通の前後照合で確認し、元 session の証明で別の窓を操作しない契約を追加しました。
