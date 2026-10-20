### Native の作業・質問・承認の表示を判定

dsh/Hermes の現在の操作表示から、作業中・質問待ち・承認待ちを既存の `act_state` に反映します。完全な入力欄があり作業・質問・承認の印が無い場合は入力待ちとします。表示が確認できない場合は不明のままとし、終了と推測しません。Claude/Codex の分類は維持します。API 世代は24です。[対応する表示と制限](../docs/native-capabilities.md#activity-classification-boundary)を参照してください。

Hermes の長い承認で見出しが画面外に出ても、現在の承認用入力欄と操作案内が揃えば承認待ちとして表示します。
