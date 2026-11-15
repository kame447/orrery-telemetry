### fresh MCPの固定依存を隔離importの前後で照合する

運用者承認済みの第三者依存manifestと固定コードを隔離Pythonから再検証し、FastMCP・MCP SDK・SQLAlchemy等の実import元を固定します。未承認の依存では固定済みと報告せず、サービスを起動しません。Dashboard API世代は変更しません。[運用上の制限](../docs/mcp-fresh-import-attestation.md)。
