### installer の Codex 探索・確認を launcher・doctor と共通化

installer が独自に持っていた Codex の探索 PATH・`--version` 確認・probe の後始末を削除し、同梱の共通処理を読み込んで判定します。候補を実行する PATH も launcher・doctor と同じ文脈（子の login shell・resolve した候補の隣の node）に揃え、nvm・npm-global 配下などで3か所の判定が揃うようにしました。承認の流れ、保存する値と形式、明示 `--codex-bin` が使えないときの失敗は変えません。候補の確認全体（保存済み候補の再確認も含む）に共有の時間上限を設け、同じ候補を二度確認しません。時間切れで確認し切れなかった場合は「何も見つからなかった」とは別の表示にして、値を書かずに止まります。
