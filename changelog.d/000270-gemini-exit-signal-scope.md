### dashboard EXIT 試験の signal 捕捉を試験内に限定

Antigravity の EXIT 試験が共有の `os.kill` を置き換えないようにし、別の cleanup の
signal が混ざる不安定さを解消した。旧形での混入と、EXIT 自身の余分な signal を
検出する負対照を追加した。製品の PID 選択・signal・起動経路と API generation は変更しない。
