### 隔離 global server の登録更新・window・contact を準備

S2a の明示した schema3 設定に9つの公開 tool と capability を追加しました。program/model を保つ登録更新、read-only の window 照合、単調な活動更新、相手 owner が承認する contact を扱います。request の canonical preimage と結果を同じ transaction に保存し、共通 client は固定 slot の UUID を引き継いで再送します。owner 別 quota、receipt を使わない touch、同じ read snapshot と write tracker を照合します。 mapped reconnect から policy の自動書込みを分け、quota/pending があっても活動更新を継続します。応答 schema は server/client の共通正本で確認し、不適合な応答では pending を消さず同 UUID の replay を待ちます。

確定した拒否は自身の pending を解除し、結果不明だけを保持します。preimage・既定値・DDL・authority/fence の重複を共通の正本へ寄せました。全件検証は起動時と明示 inspect に限り、通常の書込みと replay は対象行だけを検証します。UUID が別の意図と衝突した場合も pending を保持し、明示した operator の解消へ回します。FTS の検証用コピーは candidate 内の私有 directory に限り、成功時も例外時も削除します。次の検査は固定 directory の lock 下で、自分の命名規則と所有条件に一致する強制終了後の残留コピーだけを回収します。installer は共通 schema と fixture の不足を配備前に知らせ、更新試験も同じ配布元を含めます。payload のコピー元の宣言も一か所にまとめ、切り出した配備処理と通常 installer で共有します。

既存 schema2 は SQLite 接続前に不変で拒否し、別 workspace に source から schema3 を新規準備します。in-place upgrade・旧 authority 復帰・epoch repin は含みません。preparation と明示 quarantine は各保存境界の中断から再実行し、旧 root の書込みを自動移行・破棄しません。新 client root へ env/wrapper/hook/profile を向け直す手順と、旧 mode との contact 判定差を日英で記載しました。既定の旧 mode と S1 の契約・activation=false は維持します。送信と通知は S2b、予約は S2c、実切替は PR7 に残します。詳細は[global server の準備](../docs/global-server.md#s2a-schema3-の新規準備)を参照してください。
