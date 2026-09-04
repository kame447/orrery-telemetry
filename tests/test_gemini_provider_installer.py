"""Installer contract for the fork-only provider runtime and Gemini adapter."""
from __future__ import annotations

import os
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


EXPECTED_PAYLOAD = {
    "bin/agent-start-gemini",
    "bin/agentstack-gemini-bootstrap",
    "bin/agentstack-gemini-setup",
    "bin/agentstack-gemini-child-mail",
    "bin/agentstack-gemini-stream",
    "hooks/spawn_gemini_child.sh",
    "hooks/spawn_gemini_preregistered.sh",
    "dashboard/server.py",
    "dashboard/server_core.py",
    "dashboard/provider_runtime.py",
    "dashboard/providers/registry.py",
    "dashboard/assets/google.svg",
}


def test_provider_installer_is_shell_parseable():
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_provider_installer_dry_run_covers_runtime_gui_and_child_adapter(tmp_path):
    install_dir = tmp_path / "agentstack"
    result = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--install-dir",
            str(install_dir),
            "--dry-run",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for relative in EXPECTED_PAYLOAD:
        assert relative in result.stdout, relative
    assert not install_dir.exists(), "dry-run must not mutate the install"


def test_provider_installer_copies_dashboard_abstraction_and_adapter(tmp_path):
    install_dir = tmp_path / "agentstack"
    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    for relative in EXPECTED_PAYLOAD:
        target = install_dir / relative
        assert target.is_file(), relative

    assert os.access(install_dir / "hooks" / "spawn_gemini_preregistered.sh", os.X_OK)
    assert os.access(install_dir / "bin" / "agent-start-gemini", os.X_OK)
    assert not os.access(install_dir / "dashboard" / "assets" / "google.svg", os.X_OK)


def test_provider_installer_does_not_copy_obsolete_name_specific_dashboard_patch(tmp_path):
    install_dir = tmp_path / "agentstack"
    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (install_dir / "dashboard" / "server_gemini.py").exists()
