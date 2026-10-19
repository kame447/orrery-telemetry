### Changed

- 隔離 global client の新規起動を専用 tmux socket と正確な agent 名へ統一し、runner/provider の生存証明を分離しました。
- 終了報告・退役・local cleanup を既存の intent で再開できるようにし、process 終了後と SessionStart 前の失敗にも同じ target/replay-end 入口を追加しました。既定の旧 mode は変更しません。
