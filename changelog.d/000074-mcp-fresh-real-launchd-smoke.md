### 隔離したMac実launchdの起動・停止を検証するスモークテストを追加

本物のmacOS `launchctl` で専用の使い捨てfixture LaunchAgentを起動し、登録plist、PID、終了と解除を確認する明示opt-inテストを追加した。通常CIとMacの無指定実行ではskipし、既存サービスやMail/agentには触れない。これはOSの実操作だけの検証で、fresh MCP本体の権限・実import・送受信の受入とは区別する。[試験の範囲と操作](../docs/mcp-fresh-launchd-smoke.md)を参照。
