### A user-facing change
Gemini CLIの明示した既定モデルとeffortをcore installerの更新で保持します。
`AGENTSTACK_GEMINI_MODEL` / `AGENTSTACK_GEMINI_EFFORT` は既存設定を継承し、`--reset-settings` では初期化します。
不正な指定は書込前に拒否します。DashboardのGeminiモデル初期選択や明示effortの契約は変えません。
