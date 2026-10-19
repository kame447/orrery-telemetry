### 起動時に agent 名を clipboard へ入れる処理を削除

`agent-start`・`agentstack-codex-bootstrap`・`agentstack-gemini-bootstrap` と SessionStart hook の `set-ghostty-title.sh` は、terminal のタブ名に手で貼るために agent 名を `pbcopy` で clipboard へ入れていました。この用途は無くなったので、4か所とも削除しました。opt-in の設定も残していません。起動しても clipboard の内容は変わりません。tmux session の rename、pane metadata、managed agent list の更新は従来どおりです。
