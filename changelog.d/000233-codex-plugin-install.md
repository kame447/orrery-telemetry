### Codex plugin の導入処理を共通化

- Codex の専用 installer を再利用できる処理へ分け、共有 HOME・cache の所有情報と対象 hook の承認結果を記録します。
- 承認済みの対象定義だけを公式 API で信頼し、未確認なら `/hooks` へ案内します。Mac/Linux/WSL の隔離確認 script を追加します。本体 install への接続は後続です。
- 解除前に現在の registry と service の所有を照合します。別の root や再利用された PID、確認不能な状態では削除・停止を拒否し、途中の解除を再実行できる情報を保持します。
- Linux/WSL の service は boot ID と kernel の起動 tick で照合し、表示時刻の揺れを比較から除きます。起動途中の command は記録せず、runner の一致を再取得してから所有情報を保存します。
- macOS の終了途中に `ps` が `(bash)` を返す場合は、所有確認を済ませて signal を送った後の待機だけ、同じ UID・起動時刻と実行名の一致を確認して終了を待ちます。全 signal 直前の厳密な所有照合と、未確認時の記録保持は維持します。
