### dsh の許可された hook の公開警告を表示する

dsh の標準 tool から共通 hook を呼ぶとき、成功した hook の stdout 全体が JSON object で、非空の `systemMessage` を持つ場合だけ警告を表示します。通常の成功では表示を増やさず、生の stderr・JSON でない出力・失敗した hook の出力は表示しません。
