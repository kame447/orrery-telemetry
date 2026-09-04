from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "bin" / "agentstack-gemini-setup"


def test_gemini_mcp_setup_uses_agentstack_selected_python():
    text = SETUP.read_text(encoding="utf-8")
    assert 'PYTHON_BIN="${AGENTSTACK_PYTHON:-python3}"' in text
    assert '"$PYTHON_BIN" - > "$TMP"' in text
    assert "\npython3 -" not in text


def test_gemini_mcp_setup_replaces_config_atomically():
    text = SETUP.read_text(encoding="utf-8")
    assert 'TARGET_TMP="${TARGET}.tmp.$$"' in text
    assert 'mv -f "$TARGET_TMP" "$TARGET"' in text
