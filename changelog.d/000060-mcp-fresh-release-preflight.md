### fresh MCP専用常駐化の事前条件を固定release・権限に照らして検査

既存のMCPを変更せず、独立service label・専用port・Mail instance/送信者/宛先と、private policy・固定releaseを起動前に検査する読み取り専用部品を追加します。実プロセス、fresh client、依存環境は未検証として区別し、起動停止は行いません。Dashboard API世代は変更しません。[事前検査の範囲](../docs/mcp-fresh-release-preflight.md)を参照してください。
