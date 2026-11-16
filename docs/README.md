# ドキュメント索引

[English](README.en.md) · [README](../README.md)

Claude Code・Codex・DeepSeek・Local LLMのどれからでも始められます。まず[単独導入](getting-started.md#telemetry-単独の導入と確認)か[cockpitと共通導入](https://github.com/gyroid-eth/orrery/blob/master/README.md#クイックスタート)を選び、Claude/Codexは使うCLIへのログイン、DeepSeek/Localは[APIの初期設定](native-setup.md)へ進みます。準備後の画面操作は **Your first flight → help map → Full tour**。cockpitの`Settings → Getting started`から開けます。ここは導入・更新・困ったときに読む参照の目次です。

Telemetry 自体の操作は、header の `HELP MAP`（NETWORK では `SETTINGS` の隣）から見回せます。単独の画面でも cockpit 内の埋め込みでも、DECK / NETWORK の操作に注記します。agent の詳細パネルでは、パネル内の `HELP MAP` からパネルの操作を説明できます。

全体像: cockpit は terminal を操作する画面、Telemetry は dashboard と agent graph の画面です。cockpit は Telemetry の画面を埋め込めます。

![cockpit は terminal を操作する画面、Telemetry は dashboard と agent graph の画面。cockpit は Telemetry の画面を埋め込める。agent は tmux の中で動き、両方の画面から見え、Mail で話す](https://raw.githubusercontent.com/gyroid-eth/orrery/master/docs/images/cockpit_telemetry_relation.svg)

## はじめる

- [DeepSeek・Local LLMの初期設定](native-setup.md) — 共通導入後、dsh/Hermes・APIキー・接続設定・画面からの起動へ。新規・既存利用者とも使え、Claude/Codexのログインは不要。
- [Ubuntu WSLにAPIキーを保存する](wsl-keyring.md) — 標準GUIで鍵束を準備し、APIキーを非表示入力で保存する初回手順。

- [目的と provider・model・harness を選ぶ](getting-started.md#provider-model-harness) — 観測、API の model で作成、agent 同士の連携の3導線。[対応組合せと現在の制限](managed-catalog.md)で準備・OS・画面の範囲を確認する。
- [ORRERY cockpit](https://github.com/gyroid-eth/orrery/blob/master/README.md#クイックスタート) — 1行で両方を入れ、画面の初回ガイドから始める。
- [Telemetry 単独で使い始める](getting-started.md) — 対象者・用語と、単独 install、agent 起動、Mail の通し確認。

## 使い方

- [Dashboard](dashboard.md) — DECK/NETWORK、履歴、RESUME/EXIT、REPLAY と詳細の参照。
- [指定スマホで状況を見る](mobile.md) — 既定オフの閲覧画面、本人認証と端末制限、手動有効化と停止。
- [ChatGPT から使う MCP 接続](chatgpt-mcp.md) — loopback 限定のゲートウェイ、Secure MCP Tunnel、ペアリングと Mac 上の承認、ユーザーが行う設定。
- [ChatGPT から既存エージェントへ追加指示を送る](chatgpt-mcp-message.md) — 専用の Mail 送信者、宛先の許可リスト、プレビューと重複防止、配送状態の照合（既定無効）。
- [委任と child agent](delegation.md) — ORRERY /delegate、組み込み子との違いと渡す道具の選択。
- [Launcher と identity](launchers.md) — agent-start、名前、resume、/delegate と /log、通知の流れ。
- [Obsidian と一緒に使う](obsidian.md) — ログ・論文ノート・タスクを vault と Daily Note に置く。

## 空のMailへの切替候補

- [fresh移行・停止復旧](fresh-global-update.md) — 新しい空instanceを使う明示手順。通常設定からの私有起動・停止は確認済み。通常updateでは切り替わらず、実OS manager/Finder GUI・実provider/trust/導入・最終CIと本人在席の切替は未受入。復帰・App前面化は今回の対象外。
- [fresh送信受付の読取り照合](fresh-message-receipts.md) — 本人の受付記録を再送せず照合するfork契約。未観測を再送許可にしません。
- [fresh MCPの矛盾したJSON-RPC成功/失敗応答の拒否](mcp-fresh-rpc-envelope.md) — 段階32。success resultとerrorが共存する応答を実プロセス検証で受理しない。
- [fresh MCP実効権限フラグの厳密型照合](mcp-fresh-strict-adapter-flags.md) — 段階31。数値 `0` / `1` を真偽値の権限として誤認しない。
- [fresh MCP実プロセス検証のJSON応答一意性](mcp-fresh-unambiguous-rpc.md) — 段階30。重複JSONキーと真偽値RPC IDを拒否し、権限照合の曖昧さを防ぐ。
- [fresh MCP応答後のPID再照合](mcp-fresh-post-rpc-pid.md) — 段階29。最終RPC後のPID変化・判定不能を成功と誤認しない。観測時点の検証であり継続稼働の保証ではない。
- [fresh MCPの実プロセス照合](mcp-fresh-process-probe.md) — 現在のLaunchAgent PID、接続先、送信権限を照合するforkの検証部品。常駐化やMac本番受入は未完了。
- [fresh MCP固定releaseの事前検査](mcp-fresh-release-preflight.md) — 専用service ID・権限・private policy・固定digestを起動前に検査するfork部品。実起動や本番受入は対象外。
- [fresh MCP実プロセス・設定の照合](mcp-fresh-live-service-status.md) — 固定plist・releaseと稼働PID・実効送信権限を照合するfork段階6。常駐化とMac受入は未実施。
- [fresh MCPのLaunchAgent設定を安全に準備](mcp-fresh-service-staging.md) — 本人が確認した固定設定を新規plistとして一度だけ保存。サービス起動と既存環境の変更は行いません。
- [fresh MCPの固定コード・依存import検査](mcp-fresh-import-attestation.md) — 常駐起動前に読取り専用Mail配布物とgatewayを実Pythonで照合するfork段階8。第三者依存は未固定、サービスは起動しません。
- [fresh MCPの検証済みLaunchAgent非起動設定](mcp-fresh-attested-stage.md) — 固定gateway・Mail・依存importを同じPython内で検証してからserveする実行引数を承認digestに固定し、安全にplistを新規作成します。起動はしません。
- [固定release fresh MCPの異常終了と明示復旧](mcp-fresh-pinned-crash-recovery.md) — 段階28。Mac専用fixtureのSIGKILL後、再起動時のPID/権限/receipt永続性と二重送信防止を受入検証。本番復旧や実agent処理は未検証。
- [fresh MCPのCI子プロセス待受診断](mcp-fresh-ci-process-readiness.md) — 段階27。macOS CIで残る隔離子プロセス2件の起動失敗に対し、60秒の有限待機と秘密非露出の真偽値診断を追加。CI解消は未確認。
- [Mac実LaunchAgentでの固定release MCP受入](mcp-fresh-pinned-launchd-sdk-e2e.md) — 段階26。固定source/依存を実LaunchAgentで実行し、PID/接続所有者・SDK・Mail受理・安全な解除を確認。実agent処理やTunnelは未検証。
- [固定releaseのfresh MCP実SDK受入](mcp-fresh-fixed-release-sdk-e2e.md) — 段階25。固定source/第三者依存を同一プロセスで照合した実MCPへSDK接続し、Mail受理と改変拒否を隔離確認。launchd本番切替・Tunnel/実agent処理開始は対象外。
- [fresh MCPの隔離Dashboard接続先](mcp-fresh-dashboard-loopback.md) — 専用loopback Dashboardをplan/固定release起動/承認digestで一致させ、本番Dashboardへの誤接続を防ぐ段階24。実接続受入ではありません。
- [fresh MCPのCI用Pythonを隔離](mcp-fresh-ci-venv.md) — Mac CIで親・隔離子の依存解決を同じvenvに固定し、実import元を検査。固定releaseや本番受入ではありません。
- [fresh MCP検証済みplistの実稼働照合](mcp-fresh-attested-status.md) — plistの全バイトと稼働PID・有効なMCP権限を読み取り専用で照合する段階12。プロセス起動や配送の実証は対象外。
- [隔離fresh MCPの明示承認付き起動](mcp-fresh-attested-start.md) — 専用fixtureに限りbootstrap/kickstartし、実PID・権限を照合する段階13。Mac実接続と停止・復旧は未実証。
- [隔離fresh MCPの明示承認付き停止](mcp-fresh-attested-stop.md) — 実PIDとplistの確認・停止専用承認digestを伴う段階14。Mac実停止と復旧は未実証。
- [隔離fresh MCPの不明結果の読み取り照合](mcp-fresh-fixture-recovery.md) — 段階15。実PIDとジョブ状態を安全に観測しますが、自動再起動や実Mac復旧を保証しません。
- [macOS実launchdの隔離fixtureスモーク](mcp-fresh-launchd-smoke.md) — 段階18の明示opt-in試験。起動・PID観測・解除のみ実証し、fresh本体の受入ではない。

## install・update

- [Update](install.md#upgrade) — 既存設定を引き継いで更新する手順。
- [インストール](install.md) — 要件、単独 install、検証、upgrade/uninstall と移行手順。
- [設定](configuration.md) — AGENTSTACK_*、保存・公開範囲、model・child の設定を調べる。
- [Codex plugin 導入処理の再利用](codex-plugin-lifecycle.md) — 本体 install 接続前の共通処理、対象 hook の信頼と隔離確認。
- [Codex App 統合](codex-app.md) — optional provider として Desktop の task/subagent を載せる。
- [Antigravity / Gemini](antigravity.md) — optional provider の追加と認証・機能の境界。
- [DeepSeekの表示と残高](dsh-stage-a.md) — 段Aの歴史記録。現在の dsh / Hermes の導入は[インストール](install.md)、子の起動は provider と harness の指定が必要（[API](api.md)）。
- [常駐 agent](persistent-agents.md) — enrollment、interactive/headless 起動、credential の復旧。
- [決定論的 daemon](daemon-agents.md) — 専用 identity、正規 binding、通知待機と停止の契約。
- [Mail service の更新](agentstack-mail-update.md) — 稼働中 Mail の build を検証して差し替える運用手順。

## Windows・WSL

- [WSL2](install.md#windowswsl2で入れる) — 標準の Windows 導入は Ubuntu 内で行い、Windows のブラウザで開く。
- [native Windows helper（実験的）](windows-local.md) — 標準 WSL2 導入とは別の、community による Mail/dashboard 起動。
- [native Windows Codex launcher（実験的）](windows-codex-launcher.md) — 事前登録した子を専用 Windows tmux から起動する境界。
- [native Windows spawn（実験的）](windows-spawn.md) — native NEW AGENT の名前 catalog 取得と、SPAWN が無効である境界を読む。

## 困ったとき

- [トラブルシューティング](troubleshooting.md) — service、通知、spawn、認証、WSL の止まり方を切り分ける。
- [ローカル HTTP の信頼境界](local-http-security.md) — Dashboard と Mail が受け付ける要求元と、信頼する前提。

## 設計・開発者向け

- [Hooks と helper](hooks.md) — event、block/release/cleanup と運用 guard の仕様。
- [API reference](api.md) — route、request/response と互換世代の契約。
- [デザイン言語](design.md) — dashboard の見え方と動きの基準。
- [Embedded tour events（英語）](embedded-tour-events.md) — 埋め込み Telemetry が cockpit の checklist に実操作を通知する契約。
- [ORRERY Mail の内部構成](agentstack-mail.md) — 同梱 package の境界、provenance、cutover と正本。
- [project 廃止の契約と移行計画](project-removal-plan.md) — PR1 の candidate API と gate。既定の動作は変えず、切替は後続 PR。
- [絶対 path の予約候補](absolute-reservations.md) — PR2 の normalizer、認証付き隔離 store、client adapter と予約ごとの activity/GC。既定は旧 mode。
- [Mail と配送の記録の移行候補](namespace-state-migration.md) — PR3 の隔離 snapshot、永続 store と phase ごとの復旧。activation false。
- [保存元を保つ B1 の内部 kernel](storage-origin-b1.md) — S′ の候補解決・本人操作・pure peek。未公開で、既定の起動経路は変更しない。
- [global server の S1 準備](global-server.md) — 隔離 HTTP の登録・再接続・inbox・enrollment と authority epoch。既定は旧 mode。
- [隔離 global 予約・welcome](global-reservations.md) — S2c の唯一 SQL engine、TTL、管理復旧、試験移設。既定は旧 mode。
- [隔離 global message と通知（S2b）](global-messages.md) — 8 tool・DB 添付・純粋 inbox・signal の再照合。既定は旧 mode。
- [global runtime client の4a準備](global-runtime-client.md) — S1 の登録・復旧・待機、専用 HOME と profile の再接続。
- [Mail 更新の設計メモ](agentstack-mail-update-design.md) — 差し替え方式の理由と未決事項。操作は更新手順を参照。
- [Mail 性能 gate の設計](agentstack-mail-performance-gate.md) — 設計のみの benchmark と採否基準。常設 benchmark の実行手順ではない。
- [Claim/enrollment 設計](agentstack-mail-claim-enrollment-design.md) — product-decision ledger に対応する enrollment の設計資料。
- [第三者コンポーネント（日本語）](third-party.md) — ORRERY Mail、provider ロゴ、肖像画像の license と来歴。
- [開発への入口](../CONTRIBUTING.md) — build・検証・変更を送るときの手順。

英語版の無い文書は原語を明記しています。画像/GIFは各文書に付随する資料です。索引の網羅性は `python3 scripts/check_docs_index.py`（repository の根から）で確認できます。

- [global client の子・再開の準備](global-child-client.md) — 固定 layout、同じ登録 intent の再送、共通 end と予約 hook。
- [呼び手・中断・受け入れの表](global-child-client-contract.md) — global client の fixture から生成した契約の一覧。

- [global client の配送・起動](global-delivery-client.md) — 固定 layout、通知の再試行、明示的な復旧と provider の準備。
- [tmux consumer の共有境界](runtime-tmux.md) — transport の固定、対象 admission と修復診断。

- [指示 block の移行](instruction-migration.md) — 利用者単位への移行と復旧。

- [専用 tmux 起動・終了の受け入れ計画](r2-tb-lifecycle-plan-v3.md) — R2-Tb の確定 caller 表・schema・中断点と fixture。

- [dsh の標準 CLI 起動](dsh-thin.md)

- [Native context と helper model](native-capabilities.md) — dsh / Hermes の context 読取とモデル別の能力。
- [Native catalog and effort capabilities](managed-catalog.md)
- [隔離fresh MCPの実プロセス・SDK・Mail受入](mcp-fresh-process-sdk-mail-e2e.md) — 独立Pythonと架空agentの実HTTP受入。
- [Mac実LaunchAgentでのfresh MCP・SDK・Mail受入](mcp-fresh-launchd-sdk-e2e.md) — 段階20。隔離fixtureで実サービスのPID・SDK通信・Mail受理・停止を検証。
- [Mac実LaunchAgent再起動後のfresh受付記録と冪等性](mcp-fresh-launchd-restart-receipt.md) — 段階21。専用fixtureの正常停止・再起動後もSDKでreceiptと再送防止を検証。
- [隔離fresh MCP宛先本人の受信・既読・ACK](mcp-fresh-recipient-owner-receipt.md) — 段階22。架空recipientの本人bindingで実Mailのinbox・既読・ACKと再起動後の永続性を検証。実agent処理開始は未検証。
