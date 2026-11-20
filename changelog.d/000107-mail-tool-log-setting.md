### legacy Mail のツールログ設定を更新後も維持

通常の installer で `AGENTSTACK_MAIL_TOOLS_LOG_ENABLED=false` を保存でき、再インストール後も設定を引き継ぎます。無効な値は設定を書き換える前に拒否し、明示値、保存済みの値、既定値の順に適用します。legacy Mail への反映には保存後の標準 controller による再起動が必要です。日英の設定説明に適用範囲と手順を記載しました。
