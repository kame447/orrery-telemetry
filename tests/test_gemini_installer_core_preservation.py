from __future__ import annotations

import json
import os
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


def test_optional_provider_install_never_replaces_existing_dashboard_core(tmp_path):
    install_dir = tmp_path / "agentstack"
    dashboard = install_dir / "dashboard"
    dashboard.mkdir(parents=True)
    core = dashboard / "server.py"
    original = b"#!/usr/bin/env python3\n# operator/core-owned dashboard\nSENTINEL = 42\n"
    core.write_bytes(original)
    core.chmod(0o755)
    (install_dir / "install-state.json").write_text(
        json.dumps(
            {
                "owned_files": [str(core)],
                "owned_dirs": [str(install_dir), str(dashboard)],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert core.read_bytes() == original
    assert (dashboard / "provider_server.py").is_file()
    assert (dashboard / "service_runner.py").is_file()
    assert not (dashboard / "server_core.py").exists()
