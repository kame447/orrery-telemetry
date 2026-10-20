### 予約保護の起動環境から不要な情報を外しました

起動先の保護対象と Mail の project namespace は従来どおり維持し、予約の判定に使わない repository identity と worktree 情報を agent の環境変数へ引き継がないようにしました。古い shell・tmux に残った同名の値も引き継ぎません。追加の共有フォルダと旧設定の自動移行は変更していません。
