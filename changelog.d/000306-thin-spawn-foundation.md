### 標準の子の起動へ dsh・Hermes の接続口を追加

- 明示 opt-in の plain 世代に対し、`spawn_child.sh --harness dsh|hermes --backend deepseek|strata` から正規の登録 binding・製品 Mail proxy・実作業dirを引き継ぎ、native CLI の config と argv を生成する基盤を追加しました。各ハーネスの generator の導入は後続 PR です。
- 共通 cleanup は、予約の解放・retire・retired の確認に失敗したときに資格を残して非0を返します。native tool の小文字名と provider/credential file の参照も共通の鍵読取判定へ渡せます。
- 追加 MCP の選択は共通の tool policy を使い、native config に写せない承認は起動前に明示して拒否します。API generation は変更しません。
