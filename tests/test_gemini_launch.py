#!/usr/bin/env python3
"""Regression coverage for the Google Antigravity CLI integration."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_LAUNCHER = _ROOT / "bin" / "agent-start-gemini"
_BOOTSTRAP = _ROOT / "bin" / "agentstack-gemini-bootstrap"
_SETUP = _ROOT / "bin" / "agentstack-gemini-setup"


def _run_setup(
    config: pathlib.Path,
    *args: str,
    bearer_mode: str = "disabled",
    token: str = "",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "AGENTSTACK_HOME": str(config.parent / "agentstack-home"),
            "AGENTSTACK_GEMINI_MCP_CONFIG": str(config),
            "AGENTSTACK_MCP_URL": "http://127.0.0.1:18765/mcp",
            "AGENTSTACK_MAIL_HTTP_BEARER_MODE": bearer_mode,
            "MCP_AGENT_MAIL_TOKEN": token,
        }
    )
    return subprocess.run(
        ["bash", str(_SETUP), *args],
        cwd=_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_gemini_shell_files_parse_with_bash() -> None:
    for path in (_LAUNCHER, _BOOTSTRAP, _SETUP):
        result = subprocess.run(
            ["bash", "-n", str(path)],
            cwd=_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_launcher_uses_current_antigravity_cli_contract() -> None:
    text = _LAUNCHER.read_text(encoding="utf-8")
    assert 'AGENTSTACK_GEMINI_BIN:-agy' in text
    assert 'AGENTSTACK_GEMINI_MODEL:-gemini-3.8-flash-high' in text
    assert 'AGENTSTACK_GEMINI_EFFORT:-high' in text
    assert '--model $(printf \'%q\' "$GEMINI_MODEL")' in text
    assert '--effort $(printf \'%q\' "$GEMINI_EFFORT")' in text
    assert "GEMINI_API_KEY" not in text
    assert "--dangerously-skip-permissions" not in text


def test_bootstrap_registers_antigravity_runtime_and_model() -> None:
    text = _BOOTSTRAP.read_text(encoding="utf-8")
    assert 'ags_register_session "$PROJECT_KEY" "antigravity" "$GEMINI_MODEL" "agy"' in text
    assert 'AGENTSTACK_GEMINI_MODEL:-gemini-3.8-flash-high' in text


def test_mcp_setup_preserves_unrelated_servers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = pathlib.Path(tmp) / "mcp_config.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "keep-me": {
                            "serverUrl": "https://example.invalid/mcp"
                        }
                    },
                    "other": {"preserved": True},
                }
            ),
            encoding="utf-8",
        )
        result = _run_setup(config, "--print")
        assert result.returncode == 0, result.stderr
        rendered = json.loads(result.stdout)
        assert rendered["other"] == {"preserved": True}
        assert rendered["mcpServers"]["keep-me"] == {
            "serverUrl": "https://example.invalid/mcp"
        }
        assert rendered["mcpServers"]["orrery-mail"] == {
            "serverUrl": "http://127.0.0.1:18765/mcp"
        }


def test_mcp_setup_adds_bearer_header_only_when_enabled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = pathlib.Path(tmp) / "mcp_config.json"
        result = _run_setup(
            config,
            "--print",
            bearer_mode="enabled",
            token="secret-for-test",
        )
        assert result.returncode == 0, result.stderr
        entry = json.loads(result.stdout)["mcpServers"]["orrery-mail"]
        assert entry == {
            "serverUrl": "http://127.0.0.1:18765/mcp",
            "headers": {"Authorization": "Bearer secret-for-test"},
        }


def test_mcp_setup_uninstall_removes_only_orrery_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = pathlib.Path(tmp) / "mcp_config.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "orrery-mail": {"serverUrl": "http://old.invalid/mcp"},
                        "keep-me": {"serverUrl": "https://example.invalid/mcp"},
                    }
                }
            ),
            encoding="utf-8",
        )
        result = _run_setup(config, "--uninstall")
        assert result.returncode == 0, result.stderr
        rendered = json.loads(config.read_text(encoding="utf-8"))
        assert "orrery-mail" not in rendered["mcpServers"]
        assert rendered["mcpServers"]["keep-me"] == {
            "serverUrl": "https://example.invalid/mcp"
        }


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__]))
