### project 廃止に向けた契約と移行計画を準備

旧 scope 引数を受けて無視する candidate adapter、message ごとの reply_to と旧数字 thread の変換、名前付き旧値の読取、agent 名と window UUID の衝突・解決を追加しました。返信は元 message ごとの送信者・宛先で照合し、残る子が参照する親は purge から保留します。候補の会話・旧ラベル読取は、可視結果数と探索量の上限を分け、不透明な cursor で続きを取得できます。最終 snapshot の再検証と固定した予定 writer 集合に結び付く receipt/gate も fixture で確かめます。

これは準備の PR で、公開中の tool・project ごとの配送・DB・installer・hook は従来どおりです。新 API は明示した candidate だけで使い、gate が ready でも切替は有効になりません。詳細は[契約と移行計画](../docs/project-removal-plan.md)を参照してください。
