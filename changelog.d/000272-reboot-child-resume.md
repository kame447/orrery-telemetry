### 終了した agent を UI の RESUME で再開でき、PC の再起動で止まった子も再開できる状態に戻ります

#263 以降、dashboard の tmux の呼び出しは対象を照合してから実行します。この照合は、存在しない session への `has-session` に「見つからない」と答えず、拒否（`TMUX_TARGET_UNAVAILABLE`）していました。その結果、2つのことが起きていました。

- 終了した agent の RESUME が、例外で応答を返さず「the dashboard's answer was lost」で失敗していました。
- 再起動や強制終了で止まった子は、起動時と1時間ごとの復旧で、tmux session が無いことを確かめられずに飛ばされていました。一度も再開できる状態に戻らず、RESUME は `config_unrestorable` で拒否されていました。

session が無いかどうかは、session の一覧で判定するようにしました。一覧に名前が無ければ「無い」と読みます。一覧に名前があるときだけ従来どおり照合付きで確かめ、一覧を取れないときは「分からない」として子の state を変えません。API generation は変更しません。
