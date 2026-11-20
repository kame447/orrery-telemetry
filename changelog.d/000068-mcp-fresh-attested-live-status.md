### fresh MCP専用の実稼働照合を非破壊で追加

非起動の検証済みLaunchAgent設定と実稼働PIDを読み取り専用で照合するAPIを追加しました。plistバイト列・ファイル権限・固定コード/依存の準備時検査・MCPの応答権限を再検証し、不明な状態を拒否します。サービスの起動、停止、再起動は行いません。稼働Pythonのimport元・配送・agentの受信/処理開始は別途実証が必要です。Dashboard API世代は変更しません。詳細は[段階12の実稼働照合](../docs/mcp-fresh-attested-status.md)を参照してください。
