### A user-facing change

macOSで既存のClaude/Codex利用者指示文を更新するとき、新しい一時ファイルにOSが自動付与する `com.apple.provenance` 属性だけが増えても移行できるようにしました。元からある拡張属性、ACL、権限、指示文本文の検証はそのまま維持します。途中で止まった移行も、同じplan IDの `--resume` で安全に再開できます。
