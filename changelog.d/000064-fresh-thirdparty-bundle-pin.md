### fresh MCPの第三者依存バンドルを承認済みmanifestと照合する

専用site-packagesのファイル集合、SHA-256、配布物名・versionを確認し、運用者が別途承認したprivate manifestとの完全一致だけを受け入れます。symlink、hardlink、.pth、可変ソースや不明な配布物を拒否します。これはread-onlyの起動前検証で、本番サービスとDashboard APIは変更せず、供給元真正性と実プロセス検証は未完了です。[設計と制限](../integrations/chatgpt_mcp/DEPENDENCY_BUNDLE.md)。
