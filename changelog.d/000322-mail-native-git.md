### Mail の archive commit の CPU 負荷を軽減

- macOS の archive の stage・commit に native Git を使い、大きな index を Python で読み書きする負荷を減らします。既存の履歴・Agent trailer・commit hook・非同期 queue と、失敗時の retry は維持します。macOS 以外の処理は変更しません。

- 監査fileのstageはignore・改行変換・clean filterを通さず、生bytesと実行bit・symlinkを保持します。
