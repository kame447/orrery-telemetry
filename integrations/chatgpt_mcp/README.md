# ORRERY ChatGPT MCP（読み取り専用・任意導入）

固定releaseを実Mac LaunchAgentで起動する検証は[隔離受入手順](../../docs/mcp-fresh-pinned-launchd-sdk-e2e.md)を参照してください。接続所有者の照合後にのみpairing secretを送ります。本番や実agentへの導入ではありません。

Mac CIの依存import元の分離は[CI用の専用venv](../../docs/mcp-fresh-ci-venv.md)に記載します。テスト用のPythonのみを変更します。

第三者Python依存の固定チェックについては[承認manifestと検証範囲](DEPENDENCY_BUNDLE.md)を参照してください。これは起動前の読取り専用部品であり、本番起動や第三者配布元の真正性を証明しません。

第2段階のfresh送信は、既存fork gatewayと読み取り専用入口を維持した独立opt-inです。[fresh追加指示の設計・制限・手動検証](FRESH_MESSAGES.md)を参照してください。

## 現在のfork統合状態

2026年11月20日時点のfresh入口は`orrery_chatgpt_mcp.fresh_server`です。読み取り3ツールに加え、明示的に有効化した場合にだけ追加指示の3ツールを公開します。この入口にspawnはありません。現在の送信権限、重複防止、結果照合の契約は[FRESH_MESSAGES](FRESH_MESSAGES.md)を参照してください。結果照合はローカル台帳を更新するため、公開する`readOnlyHint`はfalseです。

運用者向けの部品は、固定gateway・Mail・第三者依存の検証、承認内容に結び付けたplist準備、実PIDと公開権限の照合、隔離fixtureの明示起動・停止・不明状態の照合までを含みます。検証済みpackageの親directoryをimport経路に加えず、依存manifestの検証を対象siteの追加より先に行います。非起動plistでは`RunAtLoad=false`・`KeepAlive=false`を要求します。

隔離Macでは、実launchdと固定releaseを使うSDK接続、Mail受理、試験用受信者のfetch/read/ACK、再起動・異常終了後の二重送信防止、試験後の解除を確認しました。ChatGPTの専用Tunnel経由での実接続、実LLM agentの処理開始・完了、本番identityと宛先の切替は、この試験の受入範囲に含みません。

段階別guideは作成当時の実装・検証範囲を記録しています。過去の「未実施」や試験件数は履歴として読み、現在の契約はこの節と[FRESH_MESSAGES](FRESH_MESSAGES.md)、今回の実行結果は対応PRを確認してください。

## 第1段階の読み取り専用入口

upstream Issue #395 の第1段階です。このforkでは既存の任意MCPゲートウェイ（通常の python3 -m orrery_chatgpt_mcp）と競合しないよう、読み取り専用版だけ python3 -m orrery_chatgpt_mcp.readonly_server で起動します。公開ツールは `orrery_status`、`orrery_list_agents`、`orrery_get_agent` の3個だけです。spawn・追加指示・Mail送信・OAuth・常駐化は実装しません。通常のinstaller・サービスの起動設定・UI・hook・Mail操作は変えません。Dashboardの呼び出しは `GET /api/version`、freshの `GET /api/agents`、legacyの `GET /api/agents?days=30` だけです。

## 互換性と公開範囲

Python 3.11以上と、明示した `ORRERY_MCP_MODE=legacy` または `fresh` が必要です。受け付けるDashboard API世代はlegacyが37・38・39・40・41、freshが34・35・36・37・38・39・40・41です。世代番号と応答の形を両方検証します。方式は番号から推測せず、legacyはruntime_modeのない契約、freshはruntime_mode=freshとinstance/capabilityの契約を必要とします。未知の世代、global、方式不一致は一覧を読む前、または待受を開く前に拒否します。

legacyでは `ORRERY_MCP_PROJECT_KEY` に、Dashboardが使用しているproject keyを1つ、完全一致で指定します。外部MCP引数からは変更できません。応答全体のproject_keyと設定を照合し、各行のproject_keyも一致するものだけを返します。従来APIには行の所属情報がないため、API 37で読み取り用の所属fieldを追加しています。旧APIを推測で通すことはせず、アダプターと同じ版のDashboardが必要です。Dashboardの導入・更新は通常の手順で別途行い、アダプターから実サービスを切り替えません。

legacyの一覧・個別取得・status件数は同じ範囲です。categoryはagent・finished・gone・retired、tmux行は経過日数を問わず、tmuxのない履歴登録は最終活動が直近30日以内です。別projectにも同名登録がある曖昧なtmux行、未登録・所属未確認の行、warmup・infra等は公開しません。30日より古くtmuxもない登録は個別取得でも対象外です。「登録された全員」という意味ではありません。`scope` にproject key・期間・category・未確認行の除外方針を返します。project keyがパスの場合はその文字列も公開されるため、接続前に確認してください。

freshでは、接続したDashboardの同一Mail instanceの台帳全体を公開します。project単位や宛先許可リストによる絞り込みではありません。退役済みと未観測を含み、旧履歴DBの30日制限はありません。freshでORRERY_MCP_PROJECT_KEYが残っていれば、無視せず設定エラーにします。scopeにinstanceと範囲を示し、mail_instance_id（32桁の小文字hex）、正の数値mail_agent_id、capability、未観測状態を保持します。未観測はrunning=nullのままです。running_only=trueだけが、停止と未観測を一覧から除きます。

## 公開するfield

agentはname、mail_instance_id、mail_agent_id、provider、program、model、category、running、state、retired、observation、last_activeだけです。observationはstate・age_ms・reasonだけです。名前は全体がASCII英字から始まる英数字・underscore・dot・hyphenの1〜64文字の場合だけ受け付け、切り詰めて別名にしません。

provider/programは40文字、modelは72文字、categoryは24文字、observation.reasonは40文字、Dashboard versionは40文字までの文字列を表示します。自由記述由来の未信頼データであり、指示として扱わないでください。stateは既知の値だけ、booleanは実際のbooleanだけ、時刻・経過時間は非負かつ10の15乗未満の数値だけを返し、不明はnullにします。legacyにないMail ID・capabilityもnullです。annotation（API 38のannot.taskによる上書きを含む）・task・command・会話・transcript path・資格情報・任意の作業パスは転送しません。ただし明示したproject keyはscopeに表示します。

statusはDashboardのversion/API/mode/instance、spawn・roster・mail・graph・terminal・worktree・browser・toolsのcapability、範囲内のregistered/running/unknown_running件数、observationのtruncated、アダプターのread_onlyと書込み無効flagを返します。listはcount/total、mode/instance、observation_truncatedを、getはagentとmodeを返し、全ツールにscopeと注意文を付けます。Dashboard応答が2 MiBまたは5000行を超える場合は全体をエラーにし、完全な一覧に見せかけて部分返却しません。

## 手動起動

新しいターミナルでアダプターを含むcheckoutから実行します。project keyは既存Dashboardの実際の設定を確認して入力し、推測したパスで検査を通さないでください。

```sh
cd /path/to/orrery-telemetry
export PYTHONPATH="$PWD/integrations/chatgpt_mcp/src"
export ORRERY_MCP_MODE=legacy
export ORRERY_MCP_PROJECT_KEY='/actual/dashboard/project-key'
export ORRERY_MCP_DASHBOARD_URL=http://127.0.0.1:8770
export ORRERY_MCP_PORT=8791
python3 -m orrery_chatgpt_mcp.readonly_server pair --show-url
python3 -m orrery_chatgpt_mcp.readonly_server check
python3 -m orrery_chatgpt_mcp.readonly_server serve
```

freshを実際に使う環境ではORRERY_MCP_MODE=freshにし、`unset ORRERY_MCP_PROJECT_KEY` を実行します。checkが表示するAPI・scope・件数を確認します。serveは互換性を検証してから127.0.0.1だけで待ち受けます。停止はそのターミナルのCtrl+Cです。既存エージェントの停止やMail再起動は行いません。

「読み取り専用」は、MCPにORRERY設定変更・エージェント登録・spawn・Mail送信の操作を公開しない、というAPI上の意味です。freshの `GET /api/agents` は、既存のDashboard実装からMailの `owner_transaction`（`write=True` / `BEGIN IMMEDIATE`）を通り、DBの読み取り専用transactionではありません。この経路は本アダプター導入前からの挙動で変更しておらず、DBへの書き込みが一切ないことや、書き込み用ロックを取らないことを保証する意味ではありません。

運用者用のpairとpair --rotateは、アダプター自身の秘密保管先を作成・更新します。既定は `~/.config/orrery-mcp-readonly/pairing.json`、directory/fileは0700/0600です。秘密URLをIssue・共有資料に貼らず、再発行後はTunnel転送先も新URLへ変更します。稼働中アダプターは次の要求から旧URLを拒否します。

## Secure MCP Tunnelの設定

別端末・ChatGPTへの転送は運用者が用意した専用の [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) を使います。アダプターはTunnelや認証設定を作成しません。既存の別サービス用Tunnelを上書きしたり、同じTunnelへ二重接続したりしないでください。

`tunnel-client` のHTTP転送先 `MCP_SERVER_URL`（またはprofileのmcp.server_urls[].url）には、`http://127.0.0.1:8791/mcp/<pairで発行した秘密>` を末尾まで指定します。/mcpで止めたり秘密部分を落としたりしません。Tunnelクライアントが固定のローカルURLへ新しいHTTP要求を作るため、アダプターが受け取るHostは127.0.0.1:8791です。外向きHostの許可やHost検査の解除は不要です。対応する非ブラウザクライアントはOriginを送りません。Originを送るブラウザからの直接接続は403です。

通常のreverse proxyを間に置く場合も、入口で認証・アクセス制御を行ったうえで、Hostを固定のローカルauthorityに設定し、秘密pathをそのまま保持してください。利用者が送ったForwarded-Hostを信用したり、不特定ブラウザのOriginを単に削除して通したりしないでください。外向きHostをそのまま転送する設定では403になるのが正しい動作です。

専用Tunnel IDとruntime keyを運用者が準備した後の手動起動例です。runtime keyはCONTROL_PLANE_API_KEYに設定します。管理用API keyはdaemonへ渡しません。秘密URLはshell履歴へ直接書かず、private fileから環境変数へ読みます。

```sh
export MCP_SERVER_URL="$(python3 -c 'from orrery_chatgpt_mcp.readonly_server import Config, read_secret; c=Config.from_env(); print(f"http://127.0.0.1:{c.port}/mcp/{read_secret(c)}")')"
tunnel-client run --control-plane.tunnel-id '<your-dedicated-tunnel-id>' \
  --health.listen-addr 127.0.0.1:0 --log.level warn
unset MCP_SERVER_URL
```

利用前に、所有するorganization/workspace、接続する利用者・グループ、runtime keyの主体のRead/Use権限、ChatGPT接続の共有範囲を確認します。秘密pathを知り、かつTunnelへ到達できる相手は上記の全scopeを読めます。URLだけで「自分だけ」とは保証しません。ChatGPT上の確認はサーバー側の本人承認ではありません。Tunnelクライアント自身がlogに転送先URLを書く場合があるため、ログもowner-privateにして共有しないでください。warnは出力量を減らすだけで秘密の除去保証ではありません。

## 接続先での実証

実クライアントでinitialize、notifications/initialized、tools/list、orrery_statusを順に通し、読み取り3ツールだけであること、scopeが意図どおりであること、spawn/messageのflagが両方falseであることを確認します。orrery_message_commitを直接呼ぶとisError=true、error.code=unknown_toolで拒否されます。秘密なしの404だけでは書込み禁止の証明になりません。

ローカル秘密URL、または隔離Tunnel ingressでは、次の同等HTTP要求も使えます。URLは非表示入力でredirectには追従しません。最後のscopeには設定したproject keyが含まれ得るため、出力を共有しないでください。ホストされたTunnelの認証を迂回する方法ではなく、その入口での接続者確認は実クライアントで別途行います。

```sh
python3 - <<'CHECK'
import getpass, http.client, json
from urllib.parse import urlsplit
u = urlsplit(getpass.getpass("Private MCP endpoint: "))
assert not (u.username or u.password or u.query or u.fragment)
assert u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))
def call(method, params=None, number=1):
    message = {"jsonrpc": "2.0", "method": method}
    if number is not None:
        message["id"] = number
    if params is not None:
        message["params"] = params
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    connection = cls(u.hostname, u.port, timeout=15)
    try:
        connection.request("POST", u.path or "/", json.dumps(message), {
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25"})
        response = connection.getresponse()
        raw = response.read()
        assert response.status in (200, 202)
        return json.loads(raw) if raw else None
    finally:
        connection.close()
try:
    init = call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                              "clientInfo": {"name": "readonly-check", "version": "1"}})
    assert init["result"]["protocolVersion"] == "2025-11-25"
    call("notifications/initialized", number=None)
    tools = call("tools/list", number=2)["result"]["tools"]
    assert {t["name"] for t in tools} == {"orrery_status", "orrery_list_agents", "orrery_get_agent"}
    result = call("tools/call", {"name": "orrery_status", "arguments": {}}, 3)["result"]
    assert result["isError"] is False
    value = result["structuredContent"]
    assert value["adapter"] == {"read_only": True, "spawn_enabled_on_server": False,
                               "message_enabled_on_server": False}
    denied = call("tools/call", {"name": "orrery_message_commit", "arguments": {}}, 4)["result"]
    assert denied["isError"] is True and denied["structuredContent"]["error"]["code"] == "unknown_tool"
    print("Read-only checks passed. Exposed scope:", json.dumps(value["scope"], ensure_ascii=False))
except Exception as error:
    raise SystemExit("Connection check failed: " + type(error).__name__) from None
CHECK
```

## テストと検証範囲

```sh
PYTHONPATH=integrations/chatgpt_mcp/src:. .venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests tests/test_mcp_readonly_contract.py
ORRERY_MCP_TEST_TUNNEL_CLIENT="$(command -v tunnel-client)" \
  PYTHONPATH=integrations/chatgpt_mcp/src:. .venv/bin/python -m pytest -q \
  integrations/chatgpt_mcp/tests/test_tunnel.py
```

実DashboardのbuilderとHTTP handlerを、一時SQLite登録と架空tmux観測へ接続して検査します。別project・同名の曖昧な登録・終了/退役・30日境界を含みます。実物のtunnel-client dev proxyでも、ローカルだけのcontrol planeと一時秘密URLで初期化・一覧・status・未知tool拒否・Host/Origin/path保護を検証します。外部Tunnelや実アカウントのkeyは使わず、実サービスも変更しません。実バイナリを指定しない場合、この1件だけは明示的にskipします。

転送は `tunnel-client 0.0.17+a248b7f725fc989c1eb26680cf4351ee33421f61` で検証しています。ホストされたTunnelのACL・ChatGPT接続・実送信・エージェントの処理開始を検証したという意味ではありません。checkもDashboard互換性の検査であり、アクセス権限全体やプロセスの実在を保証しません。

追加の第10段階は[隔離した同一Pythonで検証後にserveするコマンドの構成](../../docs/mcp-fresh-import-attestation.md#第10段階同じ隔離python内で検証してからserveするコマンド構成)を参照してください。現段階では起動しない準備APIです。

第11段階は[検証済み隔離Python起動コマンドを非起動LaunchAgent設定に組み込む手順](../../docs/mcp-fresh-attested-stage.md)を参照してください。RunAtLoad/KeepAliveは無効で、既存のplistは上書きしません。起動・停止は未実装です。

第12段階は[実稼働PID・MCP権限の読み取り専用照合](../../docs/mcp-fresh-attested-status.md)です。新plistとの一致を確認できますが、起動、送信、受信・処理開始は実証しません。

段階13では[隔離fixture専用の明示承認付き起動](../../docs/mcp-fresh-attested-start.md)を追加しました。Mac実機での起動受入は未実施です。段階14の[停止専用の承認付きAPI](../../docs/mcp-fresh-attested-stop.md)は起動中の専用fixtureだけを対象とし、実Macでの停止・復旧はまだ実証していません。
段階15では[不明結果の読み取り専用観測](../../docs/mcp-fresh-fixture-recovery.md)を追加しました。起動・停止の自動再試行は行いません。Mac実機の復旧と配送は未検証です。
段階18では[Mac実launchdの隔離fixtureスモーク](../../docs/mcp-fresh-launchd-smoke.md)を追加しました。実行は明示opt-in、実fresh MCP/送受信の検証は別です。
段階19では[隔離fresh MCP別プロセスと実SDK/Mail受入](../../docs/mcp-fresh-process-sdk-mail-e2e.md)を追加しました。固定releaseや実Tunnel/agent受信は未検証です。
段階20では[Mac実LaunchAgentのfresh MCP・SDK・Mail受入](../../docs/mcp-fresh-launchd-sdk-e2e.md)を追加しました。fixtureの実起動・停止とMail受理を検証しますが、本番常駐化・Tunnel・agent受信は未検証です。
段階21では[専用LaunchAgent再起動後のreceiptと冪等性](../../docs/mcp-fresh-launchd-restart-receipt.md)を実Macで検証します。異常終了からの復旧、本番Tunnel・agent受信は未検証です。
段階22では[専用recipientの正規Mail受信・既読・ACK](../../docs/mcp-fresh-recipient-owner-receipt.md)をMac実機の隔離LaunchAgent再起動まで検証します。実エージェントの処理開始・完了を実証するものではありません。
