### Hermes の effort を選べるように

Hermes の Chat Completions 経路では、固定版から reasoning の選択肢を読み、モデルとハーネスを選んだ時の effort に反映します。他の transport は不明のままです。未操作の時は引数を追加せず、保存済みの設定を含む Hermes の実効設定を使います。公開の既定値は推測しません。dsh・Claude・Codex の選択肢は変わりません。

DeepSeek の正式な thinking 無効化の値 `none` を含め、ハーネスの値をそのまま渡します。`max` に丸められる `ultra` は別の段として表示しません。
