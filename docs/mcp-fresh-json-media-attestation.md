# fresh MCP実プロセス検証でJSONメディア種別と非有限数を拒否する（fork段階33）

固定releaseのfresh MCPを隔離LaunchAgentから検証する `fresh_process_probe` は、認証済み接続から応答したJSON本文でもHTTPの `Content-Type` を確認していなかった。また、Python標準JSONデコーダーの既定動作では、JSON仕様外の `NaN` / `Infinity` / `-Infinity` を受理していた。そのため、標準JSONではない応答をMCPの実効権限の根拠として採用できた。

今回、initialize・tools/list・orrery_statusの3応答すべてについて `application/json` のみをJSON応答として解釈し、`application/json; charset=utf-8` も許可する。別のメディア種別、仕様外の非有限数、重複キーは、内容を公開せず検証失敗にする。JSON-RPCのMCP応答を厳格化する措置であり、他のクライアントのSSE受信機能を実装したものではない。専用Tunnelや実ChatGPT接続の受入証拠ではない。

Mac Python 3.13の合成loopbackでは、修正前に3メソッド×2種の応答不備を受理する6ケースが失敗した。修正後は対象unittest 31件成功し、`NaN` / `Infinity` / `-Infinity` の全9ケースと、正しいJSONメディア型のパラメータ付きヘッダーの受理を確認した。CI全体と独立レビューは別途確認する。

既定OFF、現在のMCP/Mail/Tunnel/稼働エージェント、本番認可、公開Dashboard API generationは変更しない。戻す場合は本段階のprobe、回帰テスト、日英文書、changelog fragmentのみをrevertする。
