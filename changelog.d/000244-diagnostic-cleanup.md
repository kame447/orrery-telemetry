### 未使用の診断 backup と restore を削除

呼出し元のない診断 backup／restore／一覧の3関数と専用 manifest、および空になる節を削除し、SQLite の raw copy を再び利用する入口を減らしました。global server の監査表は、内部 candidate manifest と rehearsal の raw hash も接続の寿命とともに明記しています。公開 tool・既定の旧 mode の動作は変わりません。
