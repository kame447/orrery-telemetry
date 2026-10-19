### session の無い宛先の古い Mail signal で health が red になる誤警報を修正

watcher は `session_not_found` の signal を再試行用に残していました。health は24時間以上古く、tmux session が無いと確認できた signal を残留数として別に報告します。生きている宛先・直近24時間・headless runtime の未配送は従来どおり数え、50件超なら red にします。測定に失敗した場合も除外しません。doctor は残留数と確認・削除のコマンドを warn で示しますが、この残留だけでは終了コードを1にしません。

片付け用の `dashboard/mail_signals.py` は既定で候補を表示し、`--prune` で削除前にも session 不在を確認して、signals 配下の古い通常ファイルだけを削除します。Mail の DB と inbox は変更しません。全数・判定対象数・残留数・session 確認可否を API に追加したため互換世代を11に上げました。
