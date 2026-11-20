# fresh MCP の検証済み起動コマンドをLaunchAgent設定へ接続する（fork段階11）

この段階は、[第10段階の隔離Python起動コマンド](mcp-fresh-import-attestation.md)と[第7段階の非起動plist準備](mcp-fresh-service-staging.md)を接続する。常駐起動・bootstrap・停止・再起動・本番切替は行わない。既存のlegacy MCP、Mail、Tunnel、agent、LaunchAgentへ影響しない。

`orrery_chatgpt_mcp.fresh_attested_stage.preview(plan, dependency, home, *, verify_release, read_release)` は第10段階の `build_command` を使って、固定gateway・Mail・第三者依存のimport元とmanifestを隔離Pythonで確認してから、その同じプロセスでserveするための `python -I -S -B -c ...` をplistに埋め込む。shellやユーザー環境の継承はない。起動コードを含むplist全体とrelease digest、source commit、Mail instance、sender/recipients、policy全文を承認digestに結び付ける。返却値に起動引数・資格情報・policyの中身は含めない。

`stage(plan, dependency, home, approval_digest, *, verify_release, read_release, is_loaded)` は改めて同じ検証を実行し、完全一致の承認と信頼済みlaunchctl照会による `is_loaded(label) is False` を必要とする。読み取り失敗、未知のloaded状態、既存ファイル、symlink、承認以降の権限・ソース・policy変更は拒否する。新規plistのみを同一ディレクトリにatomic no-clobberで発行する。書込み結果が不明な場合も勝手に再試行・削除しない。

旧 `fresh_service_stage` は `python -m ...` と `PYTHONPATH` を使う非固定構成だったため、この段階では置き換えず別APIとする。同じlabelの既存plistは上書きせず拒否する。新しいplistは `RunAtLoad=false`、`KeepAlive=false`、`Umask=0077` とし、明示的な次段階の承認管理なしで自動起動・再起動しない。 `fresh_service_status.check_running` は現状旧構成を照合するので、このplistの実プロセス検証にも未対応である。

成功時の `same_process_import_preflight=true` は準備時点の検証結果であり、`process_started=false`、`live_process_verified=false` を常に返す。plistはまだロードしない。本番での明示的なidentity/allowlist承認、実pid・listener・source照合、固定releaseからの起動・停止・復旧、実MCP SDK/Tunnelからの受入、Mail受理とagent受信・処理開始の区別は未完了である。

対象テストは `PYTHONPATH=integrations/chatgpt_mcp/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_attested_stage.py integrations/chatgpt_mcp/tests/test_fresh_runtime_command.py integrations/chatgpt_mcp/tests/test_fresh_service_stage.py` 。本テストのサービス準備はモックであり実launchdへの起動操作は行わない。実環境の事前検証では `verify_release` と `read_release` を既存の検証済み実装に固定し、`is_loaded` も信頼できる読み取り専用実装に限定する。
