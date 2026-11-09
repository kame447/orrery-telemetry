### 切れた Mail 通知が、本文の取得方法（`include_bodies=true`）を案内するよう修正

旧 Mail の `fetch_inbox` は既定で本文を返しません（`include_bodies` の既定は false）。新しい global Mail の既定は true です。一方、本文が 2000 文字を超えた通知は「Fetch inbox to read the rest」とだけ案内していたため、旧 Mail では子が引数なしで取得しても本文が返らず、全文を読めませんでした。

通知（グローバル配送と旧 watcher の両方）と、Codex 子へ渡す初期タスク文に、`include_bodies=true` を渡す案内を加えました。サーバーの既定値と、`fetch_inbox` の応答形式は変更していません。
