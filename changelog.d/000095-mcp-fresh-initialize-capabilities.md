### fresh MCP初期化時の公開capabilitiesを厳密に照合

- 実プロセス検証で`initialize.capabilities`を固定した`tools.listChanged=false`と照合し、未承認のresources/prompts/logging/experimentalや型の不一致を拒否する。通常サーバーと本番認可は変更しない。詳細は[隔離probeの初期化契約](../docs/mcp-fresh-initialize-capabilities.md)。
