# 第2段階：fresh Mailへの追加指示（fork試作、明示的opt-in）

これはupstream #395の第2段階を検討するための独立した試作です。元の `python -m orrery_chatgpt_mcp.readonly_server` は引き続き読み取り専用で、送信を有効にする設定を拒否します。新しい入口 `python -m orrery_chatgpt_mcp.fresh_server` も既定は読み取り専用です。installer、daemon、launchd、UI、既存の本番サービス、Mailの公開APIは変更しません。legacyの送信者・資格・profileは移行せず、spawnも追加しません。

本番fresh切替の受入が成立するまでは、試験用Mail instanceだけで使用します。[upstream #395の第2段階への返信](https://github.com/gyroid-eth/orrery-telemetry/issues/395#issuecomment-7195231301)は、この隔離条件での送信PRを認めています。本番切替の許可ではありません。

## 権限と送信契約

送信には、運用者が既に準備したfreshモードの保護されたclient contextと、別途作成する0600の固定policyが必要です。アダプターはupstreamの `FreshBoundClient.call('send_message', ..., request_id=...)` を直接使います。これは `agentstack_mail.fresh_client call-json` と同じ保護されたclient実装です。CLIのシェル呼出し、任意のMailツール転送、資格の再実装、Mail DBの直接読書きはしません。

policyはMail instance、送信者の安定ID、宛先の安定ID・期待する名前・`kind=to`を固定します。宛先は1〜16件です。17件以上は起動・release事前検査で拒否し、既存宛先の削除・切り詰め・置換は行いません。外部のMCP引数でcontext、credential、endpoint、sender、project、request UUIDを選択することはできません。受信者は1要求につき1体で、名前だけの宛先指定や同名の別IDへのfallbackはありません。Mailサーバーの送信transactionが現在の認証・所属・宛先名・退役・contact policyを再確認します。preview時のwhoisは送信許可の代わりではありません。

これは「運用者が許可先への送信権を接続クライアントに事前委任する」モードです。ChatGPT上の確認やpreview/commitの順序は、サーバーが人間の承認を検証しているという意味ではありません。秘密URLとTunnelへの到達権を持つ相手は、同じpolicyの許可先へ送信できます。受信者allowlistは送信範囲だけを制限し、既存のfresh読み取り機能は引き続き同一Mail instance全体を公開します。

## 事前準備と起動

既に確認済みのfresh contextと宛先IDを使います。稼働中のlegacy環境をfreshへ切り替えたり、新しい送信者を勝手に登録したりして、この手順を通してはいけません。開発用venvにはこのcheckoutの `packages/agentstack_mail` とその依存が必要です。サービス用venvを変更しません。

policyの例は次の形です。値は説明用であり、実際のcontextとMailで確認したinstance・送信者ID・宛先ID・名前へ置き換えます。推測した値を使わないでください。context自体は既存のfresh運用者手順で準備し、アダプターから生成・登録・変更しません。

```json
{
  "kind": "orrery-mcp-fresh-message-policy-v1",
  "context_path": "/absolute/private/fresh/clients/confirmed-client-uuid/context.json",
  "mail_instance_id": "0123456789abcdef0123456789abcdef",
  "sender_agent_id": 1,
  "recipients": [
    {"target_agent_id": 2, "expected_target_name": "ConfirmedRecipient", "kind": "to"}
  ]
}
```

policyはowner所有の0600通常ファイルとし、symlink・hardlink・重複JSONキーを拒否します。stateは絶対canonical pathにある専用directoryを明示し、0700で保持します。既存サービスのstateやURLをそのまま送信用へ転用しないでください。以下は既存の8790/8791 listenerを変更しない手動起動例です。Dashboard URLは実際のfresh Dashboardと照合して設定します。

```sh
export PYTHONPATH="$PWD/integrations/chatgpt_mcp/src:$PWD/packages/agentstack_mail/src"
export ORRERY_MCP_MODE=fresh
unset ORRERY_MCP_PROJECT_KEY
export ORRERY_MCP_PORT=8792
export ORRERY_MCP_STATE_DIR=/absolute/private/dedicated-mcp-state
export ORRERY_MCP_FRESH_MESSAGE_POLICY=/absolute/private/message-policy.json
export ORRERY_MCP_DASHBOARD_URL=http://127.0.0.1:8770
export ORRERY_MCP_ENABLE_SPAWN=0
ORRERY_MCP_ENABLE_MESSAGE=0 python -m orrery_chatgpt_mcp.readonly_server pair --show-url
export ORRERY_MCP_ENABLE_MESSAGE=1
python -m orrery_chatgpt_mcp.fresh_server check
python -m orrery_chatgpt_mcp.fresh_server serve
```

checkは送信せず、設定された権限とDashboardのinstanceを確認します。serveも待受開始前に照合します。送信可能なのは新しい手動プロセスだけで、常駐化はこの段階の対象外です。停止はCtrl+C、無効化はそのプロセスを止めてENABLE_MESSAGE=0で起動し直します。プロセス起動後に設定ファイルだけを書き換えて送信先を変更する運用はしません。policyやcontextの変更・失効は送信前検査で拒否します。

接続前にTunnelのorganization/workspace、共有相手、Read/Use権限、秘密URLの保管先を再確認します。Mail側の実効的な本文ログ設定と、Tunnel/アダプターのログがowner-privateであることも確認します。アダプターはHTTP本文・資格をログ出力しませんが、別プロセスであるMailやTunnelのログ設定を自動変更したり、その安全性を保証したりはしません。既存のTunnelは変更しません。

## ツールと永続状態

有効な入口は読み取り3ツールに `orrery_message_preview`、`orrery_message_commit`、`orrery_message_status` を追加します。previewは `idempotency_key`、`mail_instance_id`、`target_agent_id`、`expected_target_name`、`subject`、`body_md` を受け取り、全文・宛先・digest・preview ID・request UUID・5分の期限を返します。subjectはUTF-8で200byte以内、bodyは65536byte以内です。空文字、NULなどの制御文字は拒否し、本文のLFとTABだけを許可します。previewの保存はアダプター自身への書込みなのでreadOnlyHintはfalseです。Mailへの送信ではありません。

commitは同じ `idempotency_key`、`preview_id`、`content_digest` の3項目だけを受け取ります。本文や宛先を後から差し替えることはできません。同じキーに異なる内容は拒否します。送信開始のclaimをowner-privateなSQLite台帳へ同期保存してからMailを呼び出します。別プロセス・同時commit・再起動でもclaim済み要求を再送しません。request UUIDはMail instance・送信者ID・keyのhashから決まり、別stateを誤って使っても同じ操作がupstreamのidempotency境界で識別されます。ただし台帳を消してよいという意味ではありません。台帳を読み戻すたびに、保存本文・宛先と現在の固定policy、再計算したdigest、keyから導出したrequest UUID、previewの形式・状態・期限を照合します。操作開始時に不整合を検出した場合は`message_state_invalid`で停止し、送信も受付照会も行いません。処理中の再読取りでも整合性を確認しますが、その時点で検出した不整合では送信・照会が既に行われた可能性があるため、証拠を保全して再送を禁止します。acceptedには正しい送信者・instanceを持つ所定の受領記録、refusedには固定形式の拒否コードが必要です。状態・観測値と整合しない記録や、公開対象外の受領項目も拒否します。これはprivate台帳の破損・取り違えを検出する検査であり、同じOS利用者による任意の改変に対する署名ではありません。

acceptedにするのは、正規clientの送信応答に実message ID、対応するinstance・送信者・宛先、`decision_only=false`、`delivery_status=notification_pending`、thread・作成時刻が揃う場合だけです。empty応答やdecision-onlyの応答は受理済みと扱いません。freshの正規sendは本文・宛先・request receiptを同じtransactionで保存します。同じrequest UUIDの過去のsend結果を返す場合も、保存した件名・本文・importance・ACK要求・topicと今回の同一intent、threadと保存receiptを照合します。異常な不一致は拒否し、MCPはunknownを維持します。元のintent hashと正常な過去receiptの形式は変更せず、送信完了後の宛先退役によって過去の受理を取り消すこともありません。応答に本文自体が含まれなくても、この保存を行う正規sendの確かな応答と、本文を保存していないdecision-onlyの応答は区別します。受理はエージェントの処理開始ではなく、`execution_status=unobserved`のままです。

statusは確定済みのローカル記録を返し、unknown/sendingの場合だけ[本人の受付記録読取りAPI](../../docs/fresh-message-receipts.md)で照合します。protected clientの保存済みrequestがpreview全文と一致することを先に確認し、別本文の古い受付記録を新しい指示の成功と取り違えません。照会結果と確認できた状態をアダプター自身のSQLite台帳へ保存するため、このツールのreadOnlyHintはfalseです。accepted/refusedへの確定には確かな証拠を要求し、not_observed/unavailableの観測も保存します。同時進行の送信処理が確定済みの状態を後からunknownで上書きすることも防ぎます。Mailには読取り以外の操作をせず、replay・再送はしません。

受付記録の照会はfork拡張です。対応しない旧サーバー、通信不能、資格失効、保存内容の不一致ではunknownを維持します。not_observedは照会時点で記録が見つからないという意味だけで、並行中の送信が後で保存される可能性を否定せず、再送の許可にはしません。receipt_observationで照会結果を区別します。エージェントによる処理開始は依然としてunobservedです。previewは期限切れでも更新せず、別keyの発行による再送も自動化しません。台帳は最大10000要求で新規previewを拒否し、unknown/accepted/refusedの履歴を自動削除しません。

台帳と正規fresh clientのrequestsには本文が保存されます。directory/fileは0700/0600で、資格は台帳へコピーしません。state・context・requestsは機密データとして保全し、共有や安易な削除をしないでください。status/commitの応答には本文とローカルパスを返しません。

台帳の不整合で停止した場合は、その台帳とprotected clientのrequestsを保全して原因を確認します。台帳削除、別key発行、上限超過したpolicyの自動切り詰めで再開しません。修正前の正常な台帳は同じ形式のまま読み込めます。17件以上の旧policyは、運用者が送信範囲を明示的に見直すまで起動を拒否します。

## 無効化の確認と検証範囲

既定の読み取り入口またはENABLE_MESSAGE=0の入口では、`orrery_status.adapter` の `read_only=true`、`spawn_enabled_on_server=false`、`message_enabled_on_server=false`を確認します。tools/listは読み取り3件だけで、commitの直接呼出しはunknown_toolです。秘密なし404やhealthzだけでは権限の証明になりません。有効な入口はstatusに委任方式・送信者・宛先範囲を明示し、healthzのread_onlyもfalseです。spawnは常にfalseです。

テストは一時fresh root、専用動的port、合成登録、隔離HOMEを使用します。実fresh HTTP送信、ORMの本文と宛先保存、request UUID、再起動、応答消失、退役、contact拒否、identity破損時のfail-closed、既存読み取り・Host/Origin/path保護を検査します。製品設定や本番エージェントを触りません。MCP SDKによる初期化・ツール発見・preview/commit/statusも隔離HTTPで検査します。実ChatGPT/ホストされたTunnelからの送信や、実エージェントによる処理開始を証明するものではありません。

実launchdを使う隔離試験は、停止の失敗やタイムアウトがあっても、専用ジョブの解除と観測済みの各PIDを独立に確認します。待受ポートやログがある試験では、それらの閉鎖と秘密情報の非出力も確認します。不明な停止を再試行せず、本体の失敗理由を保って固定の確認失敗コードを追記します。この異常系の回帰試験はmacOSの実起動を必要とせず、通常のCIでも実行します。実launchdでの確認は引き続き明示opt-inの別試験です。
