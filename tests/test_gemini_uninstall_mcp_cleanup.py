from __future__ import annotations

import json
import os
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parent.parent
UNINSTALLER = ROOT / "scripts" / "uninstall.sh"


def _write_manifest(install_dir: pathlib.Path, config: pathlib.Path, command: str) -> None:
    (install_dir / "install-state.json").write_text(
        json.dumps(
            {
                "owned_files": [],
                "owned_dirs": [],
                "services": [],
                "skill_links": [],
                "gemini_mcp_config": {
                    "config_path": str(config),
                    "server_key": "orrery-mail",
                    "command": command,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_uninstall_removes_only_the_still_owned_gemini_mcp_entry(tmp_path):
    install_dir = tmp_path / "agentstack"
    install_dir.mkdir()
    config = tmp_path / "mcp_config.json"
    command = str(install_dir / "bin" / "agentstack-gemini-mcp")
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "orrery-mail": {"command": command, "args": []},
                    "keep-me": {"serverUrl": "https://example.invalid/mcp"},
                },
                "other": {"keep": True},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_manifest(install_dir, config, command)

    result = subprocess.run(
        ["bash", str(UNINSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(config.read_text(encoding="utf-8"))
    assert "orrery-mail" not in rendered["mcpServers"]
    assert rendered["mcpServers"]["keep-me"] == {
        "serverUrl": "https://example.invalid/mcp"
    }
    assert rendered["other"] == {"keep": True}


def test_uninstall_preserves_user_replaced_gemini_mcp_entry(tmp_path):
    install_dir = tmp_path / "agentstack"
    install_dir.mkdir()
    config = tmp_path / "mcp_config.json"
    command = str(install_dir / "bin" / "agentstack-gemini-mcp")
    replacement = {"command": "/user/owned/runner", "args": ["--custom"]}
    config.write_text(
        json.dumps({"mcpServers": {"orrery-mail": replacement}}, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_manifest(install_dir, config, command)

    result = subprocess.run(
        ["bash", str(UNINSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(config.read_text(encoding="utf-8"))
    assert rendered["mcpServers"]["orrery-mail"] == replacement
    assert "kept modified Gemini MCP entry" in result.stderr
