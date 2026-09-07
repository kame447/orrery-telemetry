from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "bin" / "agentstack-gemini-setup"


def _setup_env(tmp_path: pathlib.Path, target: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    home = tmp_path / "agentstack"
    runner = home / "bin" / "agentstack-gemini-mcp"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runner.chmod(0o755)

    env = os.environ.copy()
    env.update(
        AGENTSTACK_HOME=str(home),
        AGENTSTACK_GEMINI_MCP_CONFIG=str(target),
        AGENTSTACK_PYTHON=sys.executable,
    )
    return env, runner


def test_gemini_mcp_setup_uses_agentstack_selected_python():
    text = SETUP.read_text(encoding="utf-8")
    assert 'PYTHON_BIN="${AGENTSTACK_PYTHON:-python3}"' in text
    assert '"$PYTHON_BIN" - > "$TMP"' in text
    assert "\npython3 -" not in text


def test_gemini_mcp_setup_replaces_config_atomically():
    text = SETUP.read_text(encoding="utf-8")
    assert 'TARGET_TMP="${TARGET}.tmp.$$"' in text
    assert 'mv -f "$TARGET_TMP" "$TARGET"' in text


def test_gemini_mcp_setup_treats_empty_existing_config_as_fresh(tmp_path: pathlib.Path):
    target = tmp_path / "mcp_config.json"
    target.write_text("\n  \t", encoding="utf-8")
    env, runner = _setup_env(tmp_path, target)

    result = subprocess.run(
        ["/bin/bash", str(SETUP)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["mcpServers"]["orrery-mail"] == {
        "args": [],
        "command": str(runner),
    }


def test_gemini_mcp_setup_rejects_nonempty_malformed_config_without_overwrite(
    tmp_path: pathlib.Path,
):
    target = tmp_path / "mcp_config.json"
    original = "{ definitely-not-json\n"
    target.write_text(original, encoding="utf-8")
    env, _runner = _setup_env(tmp_path, target)

    result = subprocess.run(
        ["/bin/bash", str(SETUP)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "cannot parse" in result.stderr
    assert target.read_text(encoding="utf-8") == original
