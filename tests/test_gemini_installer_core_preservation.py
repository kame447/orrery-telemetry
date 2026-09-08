from __future__ import annotations

import os
import pathlib
import subprocess

from gemini_installer_fixture import seed_existing_dashboard


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


def test_optional_provider_install_never_replaces_existing_dashboard_core(tmp_path):
    install_dir = tmp_path / "agentstack"
    core = seed_existing_dashboard(
        install_dir,
        "#!/usr/bin/env python3\n# operator/core-owned dashboard\nSENTINEL = 42\n",
    )
    original = core.read_bytes()
    core.chmod(0o755)
    dashboard = core.parent

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
