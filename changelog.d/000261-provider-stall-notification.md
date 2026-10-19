### 子の model 容量停止を親へ自動通知

legacy dashboard が Codex CLI の capacity エラーによる入力待ちを継続観測し、停止した子の既存 identity から親へ ORRERY Mail を1回だけ送ります。DECK に `MODEL CAPACITY`、NETWORK に `CAPACITY` を表示します。ブラウザを閉じていても観測し、自動再開はしません。作業再開を観測するまで再通知せず、同じ停止の通知状態は dashboard 再起動後も保持します。

API 世代を18から19へ上げ、legacy agents / graph に nullable な `provider_stall` を追加しました。送達不明時は重複を避けて再送せず `delivery_unknown` を表示します。global mode は未対応・後続です。[詳細](../docs/dashboard.md#provider-停止の自動通知legacy)。

無変化の巡回では状態ファイルを書き直さず、容量停止の候補が無ければファイルや lock を作りません。Mail の明確な拒否は `failed` として区別します。古い capture で新しい停止を解除・確認することを防ぎ、未観測の終了世代を期限付きで削除します。
