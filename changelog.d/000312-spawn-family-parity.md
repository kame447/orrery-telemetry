### DeepSeek・Local を標準の子エージェント起動へ合流

DeepSeek・Local の選択したモデルと harness を catalog の能力で照合し、Claude・Codex と同じ `spawn_child.sh` へ渡します。モデル名から backend を推測せず、harness と backend を明示します。名前・親・作業場所・worktree・端末の選択と、名前による起動状況の確認は共通です。未指定の effort を試運転の値で補わず、helper は選択した model × harness が公開する同 backend の ID だけを受け付けます。native の context は専用の status 表示から読み、非表示・不明を推測で埋めません。

API generation を 22 から 23 へ更新します。標準 `/api/spawn` は DeepSeek・Local でも `child_name` / `tmux_session` を返し、managed attempt の応答を混ぜません。通常の catalog から managed 専用の表示項目を除きます。第1段階の経路の変更であり、runtime の受け入れや tool の開放の完了を示すものではありません。

起動失敗後の後片付けも共通 cleanup の結果に従います。確認に失敗したときは失敗・cleanup unknown を返し、復旧に必要な state・資格・handoff を API が削除しません。

dsh・Hermes の再開は本人の session 作成イベントの native ID を使い、同じ登録・state・profile を保ちます。履歴の推測や初期 task の再送はせず、config 生成に失敗した場合も復旧の資格を残します。
