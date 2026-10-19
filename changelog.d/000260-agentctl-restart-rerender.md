### dashboard の明示再起動で選択済み mode/config を反映

macOS の `agentctl.sh restart` が既存の launchd job を kickstart するだけで旧 mode/config を残す問題を修正しました。`start` と同じ描画・再設置の処理を使い、設定が同じ場合も job を再設置します。supervised-background の再起動手順は維持します。 この HOME に plist がなく同じ label の job が load 済みの場合は、別の install を置き換えず明示エラーで停止します。
