### Hermes の DeepSeek 接続で thinking をツール呼び出し後も引き継ぐ

固定版の Anthropic 互換 converter が署名付き thinking を除くため、Hermes の DeepSeek 公式接続は `/v1` の chat-completions 経路を使用します。主モデルと補助モデルで同じ経路を使い、effort 未指定でも reasoning を引き継ぎます。Strata・独自の接続先や他のハーネスは変更せず、試し用の effort や token 上限は追加しません。
