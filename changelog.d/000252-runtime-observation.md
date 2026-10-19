### global network の稼働状態を既存の session から読み取る

隔離 global dashboard の network に、本人・binding・PID/start/pane を照合した working / waiting、context 使用率、親子の色を表示します。観測は既存 mutex の共有・非待機読取だけで、ファイルや活動の台帳を作りません。証明できない状態と Codex App は unknown、複数 session の activity は合成しません。

capability・Lock・Mail 表示・登録回復の browser controller を共通化し、API 18 の対応能力と非秘密の operator probe を追加しました。既定の旧 mode と activation=false は維持します。

Mail の視点を選ばない network と deck でも稼働状態を表示し、確認済みの不在と観測不能を区別します。古い launch の不在 slot と退役した祖先が現在の表示を隠さないようにし、operator probe の loopback 通信では環境 HTTP proxy を使用しません。

観測だけが失敗しても Unlock・Mail・登録は維持し、その回の稼働状態を unknown に戻します。認証拒否は引き続き Lock し、観測内部の予期しない例外も unknown として隔離します。
