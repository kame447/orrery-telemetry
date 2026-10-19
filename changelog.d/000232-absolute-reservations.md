### project 廃止に向けた絶対 path 予約の候補を準備

候補の Unicode 比較は対象 filesystem の規則を read-only で確かめ、Mac の未作成 NFC/NFD 別名と glob の活動を見落とさないようにしました。Linux の別 file は区別し、規則が不明なら取得と早期回収を止めます。候補 release hook も失敗・blocked の結果では予約を保ちます。case-insensitive な filesystem の非 ASCII path は case table を保証できないため取得を拒否し、glob の探査も unknown として早期回収を止めます。

共通の path 正規化、認証と owner を確かめる候補 server、bound proxy・hook・CLI の adapter、予約ごとの filesystem/Git 活動と GC を追加しました。別 cwd の同じ相対名を分け、同一 file と glob 内の symlink の先の新規 file は競合させます。未知の探査結果では早期解放せず、TTL と owner release は維持します。候補は明示した隔離 store で試験し、既定の公開 tool・DB・installer・既存 hook は旧 mode のままです。永続化と切替は後続 PR で行います。詳細は[絶対 path の予約候補](../docs/absolute-reservations.md)を参照してください。
