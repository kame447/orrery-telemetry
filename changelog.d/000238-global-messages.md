### project 廃止に向けた message と通知の server 契約を準備

明示した隔離 schema4 に送信・返信・既読・ack・検索・topic・digest の8 toolを追加しました。本文・添付 blob・FTS・監査 receipt と通知の再照合印を同じ DB transaction で確定し、応答喪失は同 UUID で回収します。外部出力は固定 layout の signal だけに絞り、archive・Git・保存 summary の第二正本を作りません。通知の消費を1列にまとめ、旧 hint と配送 row がない過去の message は時刻を捏造せず消費済みとして import します。可視集合の keyset、巨大な旧添付の metadata 読取、準備・通知の各中断点と明示再照合を追加しました。

未通知の判定と inbox/retire の tool 定義を共通化し、旧保管物を `provenance/` に集約して purge の対象から外しました。返信件名は既存 prefix を維持し、入力だけを制限して旧方式の長い件名も引き継ぎます。再照合の未選択件数、schema keyword の値の型、記録済み MIME 型も検証し、孤立配送の拒否は準備の共通処理に一本化しました。

既定の旧 mode・S1/S2a・activation=false は維持します。新規 BCC も他の宛先と同じ signal を受けます。実 client・watcher の接続と切替は後続です。詳細は[隔離 global message](../docs/global-messages.md)を参照してください。
