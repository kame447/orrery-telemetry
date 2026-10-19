### tmux の対象照合と旧配送経路の保護

隔離 global client に、安定した ID から実際の socket・pane・PID を照合する target/capture、operator の attach、証明済みの親による固定 compact を追加しました。旧 UUID と同名・prefix・大文字小文字の別対象へ入力せず、旧 watcher・monitor・dashboard の操作でも共通の照合を使います。専用 socket の起動はまだ有効にしません。

compact は入力欄が空で待機している場合だけ受け付け、Claude の suggestion と placeholder も文字があれば拒否します。提出後の結果不明は自動再送せず、重複の可能性を明示します。既定の旧 mode と、provider の自動 compact は維持します。[対象の読取と復旧](../docs/global-delivery-client.md#explicit-tmux-target)を参照してください。

端末・ttyd の実接続と detached resume も共通の対象照合に通し、観測中の PID 再使用や不明な cleanup で他の session に作用しないようにしました。対象の同一性と compact の入力欄の証明を分け、画面の更新だけで capture・attach が止まらないようにしています。人の確認待ちは観測期限に含めません。

文書用の read-only demo も共有 consumer に接続し、合成 pane の観測を明示しました。利用者の tmux server には接続せず、従来の合成データと表示を維持します。

共有 consumer は実際の server 不在を空と区別し、tmux の外の終了でも cleanup が退役を完了します。unsafe・timeout・読取不能は保持します。不在は直接の lstat の ENOENT に限定し、親 directory の権限喪失も不明として保持します。
