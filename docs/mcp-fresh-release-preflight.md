# fresh MCP常駐化前の固定release・権限事前検査（fork段階5）

この段階ではMCPの新しい常駐プロセスを起動・登録・更新・停止しません。起動前に必要となる固定release、独立service ID、専用port、operatorが指定したMail instance・sender・recipientの一致を、読み取りだけで検査する部品を追加します。既存のlegacy MCPサービス、Tunnel、Mail、エージェントには触れません。

`orrery_chatgpt_mcp.fresh_release_plan.Plan` は、独立した `org.agentstack.chatgpt-mcp-fresh` 系のservice labelと、既存のlegacy・Mail・Dashboard用portを除く1024〜65535の専用portを要求します。digestは64桁、source commitは40桁の小文字16進数で固定します。instanceとsender・宛先の安定ID、期待する名前、kind=toは起動計画の値です。外部MCP引数では指定しません。

`inspect(plan, verify_release, read_release)` は、operator所有の0700 state dir、0600のpairing fileとpolicyを調べます。symlink、hardlink、不適切なpermission、重複JSON key、別instance・sender・recipient、未検証の絶対pathを拒否します。policy本文や資格は返しません。次に既存の `service.verify_release` と `service.read_release` を使う前提で、固定package digestとsource commitを照合します。例外は固定の拒否コードにします。

呼出し側は `service.verify_release` と `service.read_release` を明示的に渡します。検証されない代替関数を与えた結果は信頼できません。特にsource commitはrelease manifestの申告値を照合するもので、署名済みの出自証明ではありません。既存release checkはpackageのhashと所有権を検証しますが、依存ライブラリの完全性までは証明しません。

返却値の `validated=true` は、ここでの静的な事前条件が満たされたという意味だけです。`live_process_verified=false`、`fresh_client_verified=false`、`dependencies_verified=false` を必ず含めます。pairing secretが運用可能か、contextの認証が有効か、Mailサーバーが到達可能か、実プロセスが正規の起動引数で動作しているか、MCPツールが利用できるかは、この関数では証明しません。これらは後続の実プロセス照合・常駐運用・隔離E2Eで別々に確認します。

このコードは `launchctl bootstrap/bootout`、Installer、Mail送信、再送、secretの外部送信を行いません。固定releaseのコピーや起動停止CLI、実プロセスのProgramArguments照合、専用Python環境の依存固定は未実装です。これらの未実装部分を迂回して既存サービスを切り替えてはいけません。

隔離した純粋なstdlib試験は10件成功しました。MacのDesktop Commanderに接続できず、実Macのlaunchd/lsof、既存のservice.verify_releaseとの実統合、全repository CIは未検証です。実装と受入を同一視しません。
