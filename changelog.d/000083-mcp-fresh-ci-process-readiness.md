### macOS CIのfresh MCP子プロセス待受を有限時間で診断する

専用venv導入後も残るfresh MCP子プロセス2件の待受失敗に対し、隔離試験の待機上限を20秒から60秒に拡張し、失敗時に秘密を含む生ログではなく4つの真偽値だけを出す。CIがgreenになったことはまだ確認されていない。API generationは変更しない。

詳細: [日本語](../docs/mcp-fresh-ci-process-readiness.md) / [English](../docs/mcp-fresh-ci-process-readiness.en.md)
