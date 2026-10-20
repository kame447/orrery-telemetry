### Hermes のバージョン読込で Git を起動しないよう修正

Hermes の新しい v2 runtime は、固定版の install stamp を検証して保存し、バージョン読込時の Git 起動を不要にします。既存 v1 は移行せず保持します。導入後は統括が私有 template の runtime 参照を新しい generation に向けてください。installer は template を自動変更しません。
