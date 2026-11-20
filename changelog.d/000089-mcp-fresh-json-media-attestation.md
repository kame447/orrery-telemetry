### fresh MCP実プロセス検証でJSON応答のメディア種別と非有限数を厳密化

固定releaseのfresh MCPの実効権限を照合するとき、非JSONメディア種別の応答や標準JSONにない `NaN` / `Infinity` / `-Infinity` を正常と判定しないよう修正。認証済み接続のinitialize、tools/list、orrery_statusの各応答に適用し、正規の `application/json; charset=utf-8` は引き続き許可。秘密と任意応答本文を出力せず、本番MCP/Mail/Tunnel、公開Dashboard API generationは変更しない。

詳細: [日本語](../docs/mcp-fresh-json-media-attestation.md) / [English](../docs/mcp-fresh-json-media-attestation.en.md)
