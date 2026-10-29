### `orrery-chatgpt-mcp service` で ChatGPT MCP ゲートウェイを launchd に常駐（任意）

`service install` は、ゲートウェイのパッケージを内容ハッシュ名の読み取り専用リリースに複製し、ユーザーの LaunchAgent `org.agentstack.chatgpt-mcp` として常駐させます（ログイン中に動作）。待受は 127.0.0.1 だけで、起動ツールは出しません（読み取り専用）。plist に秘密は入れません。起動は `python -I -S -B` で、毎回リリースの構成と内容ハッシュを確かめてから読み込みます。最後に、接続を受けたのがジョブのプロセスだと `lsof` で確かめてから loopback で `orrery_status` を呼び、応答を確認します。`service status` / `service uninstall` と、秘密を表示しない `pair --quiet` も追加しました。tunnel-client と ChatGPT 側の設定には触れません。[docs/chatgpt-mcp.md](../docs/chatgpt-mcp.md)
