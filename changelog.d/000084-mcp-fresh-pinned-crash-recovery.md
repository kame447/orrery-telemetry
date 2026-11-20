### Mac隔離fixed-release MCPの異常終了と明示的な復旧を検証

専用LaunchAgentの実PID・権限・接続所有者を照合してからfixtureを異常終了させ、自動再起動しないこと、明示再起動後にreceiptが永続化され同じ送信要求がMailへ重複登録されないことを実Macで確認した。独立した環境でのみopt-in実行し、本番のMCP/Mail/Tunnel/agentを操作しない。API generationは変更しない。

詳細: [日本語](../docs/mcp-fresh-pinned-crash-recovery.md) / [English](../docs/mcp-fresh-pinned-crash-recovery.en.md)
