### NETWORK の親子関係を現在の Mail project に限定しました

異なる project に同名のエージェントが登録されていても、tmux の親子関係を別 project に誤って重ねないようにしました。子のセッションに明示された project と表示対象が一致する場合のみ live の親子関係を適用します。Gemini の子起動も登録時の project を tmux に明示します。既存の Mail 認可、DB、project の解決規則は変更していません。
