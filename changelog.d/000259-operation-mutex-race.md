### client の mutex file を作成途中に読むと壊れて見える race を修正

client root の operation mutex と local mutex は、file を `O_EXCL` で作ってから JSON を書きます。その間に別の caller が同じ file を読むと、空の file を読んで `PRIVATE_JSON_INVALID` で失敗していました（同じ client root を並行して prepare したときなど）。

作成の方法は今までと同じ（同じ path への `O_EXCL`）にしたまま、読む側が、内容が揃うまで最大10秒待つようにしました。一度作られた inode は置き換えないので、更新の途中で旧版の client と重なっても、同じ inode の lock で排他します。作成者が書込みの前に止まって空の file が残った場合は、`MUTEX_INCOMPLETE`（読取専用の観測は `OBSERVATION_BUSY`）で止まります。client はこの file を自動で消しません。その client root を使う process がすべて止まっていることを確かめてから、operator がその file を消してください。手順は[global runtime client](../docs/global-runtime-client.md)にあります。
