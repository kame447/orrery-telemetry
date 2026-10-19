### project 廃止に向けた Mail と配送の記録の移行候補を準備

明示した隔離 snapshot から、複数 project の Mail と配送の DB、archive・添付・signal・履歴・window・binding を統合する候補を追加しました。ID・token・read / ack・監査・旧 thread の reply_to・配送の全 status と試行、policy と backoff を保存し、最終差分の反映後にも全内容を照合します。同名 agent・window UUID・曖昧な予約は利用者の決定を要求します。全 phase と pointer 更新直後の強制中断から再実行でき、新しい書込みの後の rollback は拒否します。

metadata を省略した旧 per-message signal も file 名の ID と宛先を照合して移行し、同 ID の旧単一 signal との共存は別の出力で保持します。無効な signal は計画に表示します。予約の Mail 活動は日時を UTC の時点に変換してから最新を選び、offset の表記差で早期解放しません。

永続 candidate で返信と purge、予約、配送の世代確認を試験します。既定の公開 tool・実 DB・installer・hook・daemon は旧方式のままで、activation は false です。切替は private workspace 内の練習のみです。実サービスの writer 制御・managed block・archive と signal の配送配線・非 ASCII path の制限解決は後続に残ります。詳細は[記録の移行と復旧](../docs/namespace-state-migration.md)を参照してください。
