### 未リリースの変更を PR ごとの断片に分けました

未リリースの変更は `changelog.d/` に1 PR につき1ファイルを追加します。release 時に `scripts/changelog-release.py` が6桁の PR 番号の降順で版の節にまとめ、使った断片を削除します。`CHANGELOG.md` の同じ節を各 PR が編集して衝突することを避け、直接の Unreleased 追加は検査で拒否します。VERSION と API の互換世代の決め方は変えません。
