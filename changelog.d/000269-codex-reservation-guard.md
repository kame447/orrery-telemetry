### Codex の apply_patch に予約 guard を追加

optional ORRERY plugin の承認済み PreToolUse hook が、予約の無い patch を編集前に拒否します。全対象 path と移動元・移動先を Claude と共通の guard で検査し、legacy mode の他者 exclusive 予約との重複は予約者を示して拒否します。doctor は guard の導入・有効化・承認状態を報告します。core と plugin を更新後、人が `/hooks` で review・承認し、新しい Codex process を起動してください。shell 経由の書き込みは対象外です。API generation の変更はありません。
