### 起動先と共有フォルダの予約保護を維持します

legacy managed 起動は実際の Git worktree（Git 外は作業フォルダ）と、明示した `AGENTSTACK_EXTRA_PROTECTED_ROOTS` を保護します。古い shell/tmux の計算済み root や Git の作業場所指定を再利用せず、coordination namespace の選択は変更しません。child、resume、persistent 起動にも同じ判定を適用し、custom warm pool は作業場所を保証できないため既存の cold 起動へ進みます。新しい pool 通信規約は追加しません。

install/update は保存済みの旧保護rootを一度だけ新設定へ移し、空の明示値と順序を保ちます。移行元・値・保存先は dry-run でも確認できます。shell/tmux だけに残った異なる旧値は取り込まず警告します。更新前に作業を完了するか予約を解放し、更新後に関係する session をまとめて再起動してください。Mail の登録・認証と global runtime の契約は変更しません。
