# fresh MCPの固定コードと依存関係を読み取り専用で検証する（fork段階8）

この段階は、後続の常駐起動手順に必要なimportの証拠を作るための、実サービスを起動しない事前検査です。PR #62のplist準備に続く独立部品として実装します。実際のlaunchctl bootstrap、Mailへの通信、エージェントへの送信、本番設定の更新は行いません。

`fresh_import_attest.check(plan, dependency, verify_release=service.verify_release, read_release=service.read_release)` を信頼済みhost操作から呼びます。まずPR #60の静的検査で、専用label、Mail instance、送信者・宛先、pairing・policyの権限、固定gateway releaseのdigestとsource commitを照合します。Mailについては運用者が別に指定した、所有者が同じで書込み不可のPython packageを全ファイル名・内容からSHA-256で照合します。git checkoutの書換可能なMailソースは、固定された配布物の代わりにはできません。アダプターが勝手に新しいMail DB、資格、送信者を作成することもありません。

次に指定されたPythonを `-I -S -B` で子プロセス起動し、gatewayとMailの両packageの全Pythonファイルをimport前に再照合します。`site`と`.pth`の自動実行を避け、信頼された開発・運用環境の`site-packages`を明示的にsys.pathへ加えます。`orrery_chatgpt_mcp.fresh_server`、`agentstack_mail.fresh_client`、`fresh_message_receipts`、FastMCP SDKを実際にimportし、先頭3モジュールが指定したファイルからロードされたこと、`FreshBoundClient.receipt`と`get_message_receipt`の契約が存在することを検証します。環境は必要最小限に固定し、AGENTSTACK_*、秘密、セッション情報、任意のPYTHONPATHを子へ渡しません。失敗時はstderrや機密パスを転記せず、固定コードで拒否します。

成功応答の`gateway_import`、`mail_import`、`receipt_contract`、`import_origins_verified`はtrueです。一方、`third_party_pinned`、`live_process_verified`、`service_started`はfalseのままです。外部ライブラリのversion/hashはまだ完全に固定されておらず、import成功だけでは供給元の真正性や後続の起動プロセスのimport元は証明できません。この検査を単独で常駐起動・完全な依存関係検証の許可として使わないでください。

運用向けに必要な`ImportPlan`は、読み取り専用のMail packageの絶対パス、その`package_digest`、明示した`site_packages`を保持します。これらはMCPツール引数から指定させません。例えば既存releaseを使用する場合、Mail artifactをowner管理の読取り専用配布ディレクトリへ別途準備してから、`.venv`の`python`実体と配布物を指定します。開発用チェックアウトを直接固定releaseとして使わず、事前検査で`dependencies_verified=true`と偽らないことが条件です。

隔離Macの実PythonとFastMCP依存を使い、一時ディレクトリに正規gateway releaseとMailの読み取り専用コピーを作る試験を実施します。変更されたdigest、欠落したreceipt module、書込み可能・symlink、外部siteの欠落、subprocess失敗時の秘密非露出を検証します。これは実Macでのimport検査であり、launchd常駐起動や実サービスの送信受入ではありません。
