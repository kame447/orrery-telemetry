### fresh MCPのCI実行環境を隔離

macOSの全Pythonテストジョブで専用venvを作成し、親pytest・import照合用の隔離子・実MCP子プロセスが同一の依存環境を参照するよう修正しました。依存の実import元を事前検査し、別HOMEや`-I -S`による見かけ上の失敗を防ぎます。本番環境の依存、MCPやMailの設定には触れません。API世代の変更はありません。
