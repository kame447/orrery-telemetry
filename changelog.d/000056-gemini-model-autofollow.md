### Geminiのモデル自動追従とHydrogen xhighの起動を修正

サービスのPATHに ~/.local/bin がなくても、起動時と同じ探索経路・実行環境でAntigravity CLIのモデル一覧を取得します。Dashboardと3種類のGemini起動スクリプトはxhighに対応し、系列ごとに存在するeffortだけを表示します。モデルとeffortの切替を登録・プレビュー・実行へ一致させ、新しい正式Geminiモデルは固定一覧を編集せず検出できます。明示許可リスト、既定モデル、取得制限とキャッシュは維持します。

既存環境の更新ではcoreだけでなく同じrevisionのoptional Gemini providerも一式更新してください。[導入・更新手順](../docs/antigravity.md)に追記しました。API generationの変更はありません。
