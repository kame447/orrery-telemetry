### upstream 更新と既存のアクセス設定・独自機能の維持

upstream の 2026-11-23 までの Claude Code 承認画面判定、Codex 表示・起動、legacy watcher の Mail 配送、スマホのプレビュー・Markdown 表示、agent の担当ロール表示、停止した子の権限変更再開を同期しました。独自の ChatGPT MCP・Gemini/Antigravity・Codex hook trust を維持します。Codex の既定 sandbox は workspace-write のままで、Full Access は明示設定時のみ有効です。

新しい Mail の画面操作スキーマ JSON とそのライセンスを、ChatGPT MCP の送信 helper の不変パッケージに内容ハッシュ付きで含めます。許可していない追加ファイルや改ざんは従来どおり拒否します。
