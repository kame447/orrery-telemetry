from __future__ import annotations

import os
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


def test_invalid_core_manifest_fails_before_provider_installer_mutates_dashboard(tmp_path):
    install_dir = tmp_path / "agentstack"
    dashboard = install_dir / "dashboard"
    dashboard.mkdir(parents=True)
    server = dashboard / "server.py"
    original = "# current installed dashboard\nSENTINEL = 'untouched'\n"
    server.write_text(original, encoding="utf-8")
    (install_dir / "install-state.json").write_text("{not-json\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid core install manifest" in result.stderr
    assert server.read_text(encoding="utf-8") == original
    assert not (dashboard / "server_core.py").exists()
    assert not (install_dir / "bin" / "agent-start-gemini").exists()
