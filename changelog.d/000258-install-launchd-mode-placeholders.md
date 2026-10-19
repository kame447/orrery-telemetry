### launchd の dashboard 設定を install・再起動のどちらでも正しく書き出す

installer が runtime mode と global server config の placeholder を未置換で plist に残し、dashboard が `RUNTIME_MODE_INVALID` で再起動を繰り返す問題を修正しました。`agentctl.sh` による書き出しでも、spawn 先、Codex の子の設定・実行ファイル・model catalog、portrait、persistent profile の計 11 項目を正しく引き継ぎます。

template の全 placeholder と両 plist writer の置換集合を照合し、隔離 HOME で実際に生成した launchd plist / systemd unit に未置換 token がないこと、設定値と XML の特殊文字が保たれることを回帰試験で確認します。API generation の変更はありません。
