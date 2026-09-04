from __future__ import annotations

import dashboard.provider_server as server


def test_gemini_runtime_is_live_while_agy_owns_the_tmux_pane():
    assert server.classify(
        "GeminiCurie", "agy", "", True, program="antigravity"
    ) == "agent"


def test_gemini_runtime_is_finished_after_agy_exits_to_a_shell():
    assert server.classify(
        "GeminiCurie", "zsh", "", True, program="antigravity"
    ) == "finished"
