### fresh MCPの送信権限フラグを型まで照合

fresh MCPの実プロセス照合で、`read_only`・`spawn_enabled_on_server`・`message_enabled_on_server` の値だけでなく、JSONの真偽値型であることを必須にする。Pythonが `0 == False`、`1 == True` と扱うため数値フラグを正しい権限として誤認する問題を防ぐ。修正前に拒否されなかった整数3ケース、修正後に拒否できた整数・浮動小数点の計6ケースを回帰テストに追加した。公開API generationは変更しない。

詳細: [日本語](../docs/mcp-fresh-strict-adapter-flags.md) / [English](../docs/mcp-fresh-strict-adapter-flags.en.md)
