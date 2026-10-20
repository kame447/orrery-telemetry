### dsh を標準の CLI と設定で起動する準備

dsh の provider・model・effort・MCP・指示・skill を native profile へ投影し、標準の登録・鍵読取・予約 hook を tool の実行前へ接続します。鍵は credentials plugin が Keychain から一度だけ memory へ取得します。旧 managed の試し用上限・egress・専用の pane 入口は使わず、patch の無い別の固定 runtime 世代を用意します。旧世代と履歴は移行中も保持します。[設定と制限](../docs/dsh-thin.md)を参照してください。
