from __future__ import annotations

import json
import os
import pathlib
import subprocess

from gemini_installer_fixture import seed_existing_dashboard


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


def test_configure_mcp_records_exact_owned_entry_for_uninstall(tmp_path):
    install_dir = tmp_path / "agentstack"
    seed_existing_dashboard(install_dir, "# installed core\n")
    config = tmp_path / "gemini" / "mcp_config.json"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "AGENTSTACK_GEMINI_MCP_CONFIG": str(config),
    }

    result = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--install-dir",
            str(install_dir),
            "--configure-mcp",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    command = str((install_dir / "bin" / "agentstack-gemini-mcp").resolve())
    manifest = json.loads((install_dir / "install-state.json").read_text(encoding="utf-8"))
    assert manifest["gemini_mcp_config"] == {
        "config_path": str(config.resolve()),
        "server_key": "orrery-mail",
        "command": command,
    }
    rendered = json.loads(config.read_text(encoding="utf-8"))
    assert rendered["mcpServers"]["orrery-mail"] == {
        "command": command,
        "args": [],
    }
