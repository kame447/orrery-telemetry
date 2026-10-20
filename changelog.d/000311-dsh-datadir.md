### dsh の子ごとに TUI の model と履歴を分離

dsh の TUI に data dir の env の入口を1行だけ追加し、子ごとの永続 dir と起動時の model 選択を設定します。子Aの `/model` が子Bや利用者の dsh に影響しなくなります。HOME 全体は変えません。専用の `plain-datadir` 世代を作り、旧い世代を上書きしません。[起動と状態の保存](../docs/dsh-thin.md)を参照してください。
