# fresh MCP専用LaunchAgentの実稼働照合（fork段階12）

段階11で作成する非起動の検証済みplistに対し、実際に応答するプロセスのPIDと許可を読み取り専用で検査する。サービスをロード・起動・停止・再起動せず、既存MCP、Tunnel、Mail、agentを変更しない。

`orrery_chatgpt_mcp.fresh_attested_status.check_running(plan, dependency, home, *, verify_release, read_release, read_secret, probe)` はまず段階11の `_candidate` で固定release・Mail・第三者依存のpreflightを再計算し、期待したplistバイト列と実ファイルが完全一致することを確認する。plistは所有者本人・0600・単一ハードリンク・symlinkなしでなければ拒否する。その後、privateなpairing secretを取得し、`Authority(instance, sender, recipients)` を使い実MCPの権限を照合する。

`probe` は段階4の `fresh_process_probe.probe` を想定する。信頼済み `launchctl print` のloaded plist・PIDと、現に確立したloopback TCP接続の所有PIDを一致させてからpairing secretを送信する。MCP initialize、notifications/initialized、tools/list、orrery_statusで6 tool、mode=fresh、Mail instance、送信者・許可宛先、message有効、spawn無効を検証する。検証直後にpreflightとplistを再照合し、変更があれば拒否する。

結果は検証済みPIDと非秘密の権限概要だけを返す。失敗時は固定エラーコードのみ。 `runtime_import_provenance_verified=false` は、コードの起動前検証が実稼働Pythonのimport元を直接観測した証拠ではないことを表す。 `delivery_observed`、`agent_receive_observed`、`agent_processing_observed` もfalseである。プロセスは照合直後に終了・交代しうるため、これはその時点の観測であり常時保証ではない。

現段階では実Macの専用freshジョブを起動しておらず、実MCP/Tunnel/agent受入を完了したわけではない。 `read_secret` はprivateな状態ディレクトリの秘密を取得する信頼済み実装とし、`probe` を任意のネットワーク接続実装に差し替えない。本番切替にはupstream受入と本人のidentity/allowlist確認が別途必要である。

隔離テスト: `PYTHONPATH=integrations/chatgpt_mcp/src:. .venv/bin/python -m pytest -q integrations/chatgpt_mcp/tests/test_fresh_attested_status.py` 。合成probeはliveプロセスの実証ではない。段階11の[非起動設定](mcp-fresh-attested-stage.md)を参照。