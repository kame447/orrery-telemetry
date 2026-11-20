# fresh MCP常駐プロセスの実効権限照合（fork段階6）

この段階は、保存されたlaunchd設定と実際のMCPプロセスを照合する読取り専用部品です。新しいserviceのinstall/start/stop/update、legacyの変更、fresh本番切替、Mail送信は行いません。前提のPR #59（実TCP接続のプロセス照合）とPR #60（release・policy事前検査）を積み重ねる構成です。

`orrery_chatgpt_mcp.fresh_service_status.check_running(plan, plist_path, *, verify_release, read_release, read_secret, probe)` は、運用者が固定したplanとplistを受け取ります。4つのcallableは運用者側の信頼済み依存であり、外部MCP要求から差し替えさせる設計ではありません。特に`probe`はPR #59の`fresh_process_probe.probe`でなければ、実プロセスの安全性を証明できません。

まずPR #60の`fresh_release_plan.inspect`によるstate/policy/release digestの確認を行います。次に、絶対canonical pathのplistをO_NOFOLLOWで読み、ownerの通常ファイルであること、hardlinkがないこと、group/worldに書込み権限がないこと、64KiB以下であることを検査します。plistはLabel、ProgramArguments、EnvironmentVariables、RunAtLoad、KeepAlive、Umaskの6つのキーだけを許可します。余分なlaunchdのsocket、起動トリガー、別の実行コマンドは拒否します。

固定した実行引数は`[python, "-m", "orrery_chatgpt_mcp.fresh_server", "serve"]`です。環境変数は専用releaseを指定するPYTHONPATH、freshモード、pairing認証、送信有効、spawn無効、専用port/state/policy、loopback Dashboard URLの9項目だけを完全一致で許可します。`RunAtLoad=false`、`KeepAlive=false`、`Umask=0077`以外は拒否します。ログイン時の自動起動と自動再起動を認めず、起動には別途検証した明示的な手順が必要です。これらは後続installerのための契約であり、この段階でplistを作るわけではありません。

静的条件を満たしてからpairing secretを読み、43文字のURL-safe形式だけをPR #59のprobeへ渡します。probeはlaunchctlの実PID、lsofが証明する接続相手、同一ソケット上のMCP初期化・tools/list・orrery_status、Mail instance・sender/recipient・許可ツールと送信権限を検証します。失敗は固定コードで拒否し、秘密URL・資格・任意の応答本文を返しません。

成功結果は`release_integrity_at_rest=true`、`live_process_verified=true`、`effective_authority_verified=true`を含みます。一方、`fresh_client_delivery_verified=false`、`process_import_provenance_verified=false`を明示します。ファイルが現在一致することは、動作中Pythonがどのバイト列をimport済みか、実エージェントが受信・実行したかを証明しません。Mail受理、通知、受信、処理開始、完了の区別は維持します。

確認したのは隔離Linuxの純粋なstdlib合成テスト16件で、通常実行と`python -O`の双方で成功しました。launchd/lsofおよび実freshリリースとのmacOS統合、GitHub Actions、完全な受入、専用serviceのインストール・ロールバックはまだ実施していません。実環境を推測して接続や本番更新を行わず、次の段階で別のservice管理PRとして検証します。