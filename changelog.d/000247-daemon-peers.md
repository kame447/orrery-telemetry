### daemon binding の相手を一覧にする（orrery-daemon-binding-v2）

固定 daemon の profile の単数 `peer_name` / `peer_id` を、name と数値 ID の組の一覧 `peers`（1〜16件）に置き換えました。旧 v1 profile は一覧と並べて読まず `profile-legacy-v1` で止め、operator が1回書き換えます。whois と送信は一覧の相手ごとに数値 row を照合し、一覧外の送り主・別 project は ACK 前に拒否します。一覧の name で数値 ID が違う Mail は保留用にそのまま返し、1人の入替で他の相手を止めません。相手が Mail に無いときは `daemon-peer-missing`、別 ID は `daemon-peer-changed` とし、`PEER_STATUS` だけがこの2つを「その相手だけ使えない」と定めます。他の失敗は障害のままです。name は Mail の正規形（英字で始まる英数字）に揃えました。global client の profile と公開 Mail tool は変わりません。
