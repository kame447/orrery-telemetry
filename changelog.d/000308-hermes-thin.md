### Added

- 標準の子起動から上流の Hermes CLI を使い、子ごとの設定 overlay と種類ごとの
  永続 profile で MCP・指示・skills・主と補助の model・明示した session ID を渡す。
  task や session の上限は追加しない。
- native の tool を実作業ディレクトリから共通の鍵読取・登録・予約の hook に通し、
  編集成功後は予約を解放する。既存の owned な Hermes v2 は再検証して plain の識別を付ける。
- 子専用の provider 設定で、永続 profile に残る旧宛先や保存済み認証が選択した
  route・key_cmd に混ざるのを防ぐ。既存 profile と記憶は書き換えない。
- 補助処理と delegation に残る旧宛先・認証の参照も子の overlay 内だけで空にし、
  選択した provider の鍵を旧宛先へ継承しない。
