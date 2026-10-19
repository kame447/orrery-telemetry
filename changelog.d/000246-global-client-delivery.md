### 隔離 global client の起動・配送を接続

起動終了の部分保存は同じ解消で完了するまで次起動を拒否します。古い配送 intent と個別の異常は他の通知を止めず、待機中の不要な取得を避けます。通知 event / 2秒 poll と既存 App 設定 reader を共有し、固定 provider 導出設定は gate 内で再生成します。

空になった通知 slot は既存 lock の出口で削除し、以前に残った空 slot も1回の走査で片付けます。並行 prepare の intent を保持し、完了した通知の総数に比例する hook の遅延を取り除きました。数値互換は先頭0のない10進と5境界に限定し、先頭0を旧 watcher は8進、global は10進で扱う差を日英に明記しました。

通知 intent の列挙は候補名だけにし、個別の不正 slot を診断へ出して他の通知を継続します。importance 未満・自己送信の intent は対応待ちと数えず、hint が消えたら片付けます。Claude の生成 hooks を既定 settings.json から分離し、Gemini の既存設定は Mail entry だけ更新します。旧 shell の数値検証との境界比較を追加し、signals directory が未作成なら作らず待機します。

既定の旧 mode と activation=false を維持し、新規 window の owner 束縛、global proxy の bootstrap、provider の起動 receipt と通知 consumer を接続しました。consumer は client 側の固定 layout に置き、既存 server 設定との isolation・binding 照合で分岐を防ぎます。window は context、現在の process は既存 session 記録、配送完了は server の notification fact に統一しました。注入や bot の応答が不明なら同 intent を再試行し、確定後は completion だけを再送します。起動の不明状態は status の digest を使う明示解消へ戻せます。persistent の実行設定は固定 profile の typed execution を operator が指定します。dashboard の明示 global 実行入口に合わせ API 世代を14にしました。
