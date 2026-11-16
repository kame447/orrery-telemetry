### 固定releaseの実MCPを隔離して起動しSDK・Mailの動作と改変拒否を受入確認

固定されたgateway、Mail source、第三者依存の専用コピーを使い、同一Pythonプロセス内でソース・import元を照合してから起動するfresh MCPの受入テストを追加した。架空agentのMail受理・receipt・重複送信防止を実SDKで確認し、固定releaseを改変した後は同じコマンドが待受前に拒否されることを確認する。既定ではskipする隔離試験であり、本番サービス・Tunnelや実agent処理開始の証明ではない。API generationに変更なし。

詳細: [日本語](../docs/mcp-fresh-fixed-release-sdk-e2e.md) / [English](../docs/mcp-fresh-fixed-release-sdk-e2e.en.md)
