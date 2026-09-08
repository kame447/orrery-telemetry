from __future__ import annotations

import json
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


def test_incompatible_dashboard_core_fails_before_copying_provider_payload(tmp_path):
    install_dir = tmp_path / "agentstack"
    dashboard = install_dir / "dashboard"
    dashboard.mkdir(parents=True)
    server = dashboard / "server.py"
    original = "# syntactically valid but too old for provider overlays\nPORT = 8770\n"
    server.write_text(original, encoding="utf-8")
    (install_dir / "install-state.json").write_text(
        json.dumps({"owned_files": [], "owned_dirs": []}) + "\n",
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

    assert result.returncode != 0
    assert "incompatible ORRERY core dashboard" in result.stderr
    assert "Update/reinstall ORRERY core" in result.stderr
    assert server.read_text(encoding="utf-8") == original
    assert not (install_dir / "bin" / "agent-start-gemini").exists()
    assert not (install_dir / "dashboard" / "provider_server.py").exists()
