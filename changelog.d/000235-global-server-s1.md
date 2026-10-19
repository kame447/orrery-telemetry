### project 廃止に向けた隔離 global server の登録・復旧能力を準備

明示した一時設定でだけ起動する S1 server を追加しました。実 HTTP の登録・既存 token の再接続・自己 inbox・window owner 照合と管理 socket の enrollment を、stable ID と世代で確認します。candidate 世代、書込みカウンタ、runtime authority epoch を分け、切替中や退役した root の操作を拒否します。既定は旧 mode の25 tool、activation は false のままです。送信・返信・contact・予約・archive/signal writer の結線は S2 に残します。詳細は[global server の準備](../docs/global-server.md)を参照してください。

SQLite の main DB と sidecar の symlink・複数 hardlink・owner/mode を初回と各操作の open 前・直後・commit 前に照合し、検査時に見つけた危険な状態を拒否します。起動後の main DB の hardlink も、別名が残っていて検査時に複数 link を検出できる間は拒否し、別名経由で commit した WAL を保持します。別名を削除した後に残った WAL は対象外です。協調 writer の共通 lock を対象とする検査で、同じ UID の非協調 process が検査と open/commit の間に path を変える競合は残ります。起動時の authority lock の inode を固定して差し替えを拒否し、非 ASCII の保存済み credential も同値で認証できます。日英の案内に passthrough の起動前提、実 HTTP・管理 socket の合成 fixture を自分で確かめる手順と、残余競合の境界を追加しました。
