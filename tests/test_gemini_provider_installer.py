"""Installer contract for the fork-only provider runtime and Gemini adapter."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


EXPECTED_PAYLOAD = {
    "bin/agent-start-gemini",
    "bin/agentstack-gemini-bootstrap",
    "bin/agentstack-gemini-setup",
    "bin/agentstack-gemini-mcp",
    "bin/agentstack-gemini-child-mail",
    "bin/agentstack-gemini-stream",
    "hooks/spawn_gemini_child.sh",
    "hooks/spawn_gemini_preregistered.sh",
    "dashboard/provider_server.py",
    "dashboard/provider_runtime.py",
    "dashboard/provider_classification.py",
    "dashboard/provider_launch_tracking.py",
    "dashboard/providers/registry.py",
    "dashboard/service_runner.py",
    "dashboard/assets/google.svg",
}


def _seed_existing_dashboard(
    install_dir: pathlib.Path, text: str = "# existing ORRERY dashboard\n"
) -> pathlib.Path:
    server = install_dir / "dashboard" / "server.py"
    server.parent.mkdir(parents=True, exist_ok=True)
    server.write_text(text, encoding="utf-8")
    server.chmod(0o755)
    manifest = install_dir / "install-state.json"
    manifest.write_text(
        json.dumps(
            {
                "owned_files": [str(server)],
                "owned_dirs": [str(server.parent), str(install_dir)],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return server


def test_provider_installer_is_shell_parseable():
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_provider_installer_requires_existing_core_dashboard(tmp_path):
    install_dir = tmp_path / "agentstack"
    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir), "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "install ORRERY core first" in result.stderr


def test_provider_installer_requires_core_manifest(tmp_path):
    install_dir = tmp_path / "agentstack"
    server = install_dir / "dashboard" / "server.py"
    server.parent.mkdir(parents=True)
    server.write_text("# core\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir), "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "install-state.json" in result.stderr


def test_provider_installer_dry_run_covers_runtime_gui_and_child_adapter(tmp_path):
    install_dir = tmp_path / "agentstack"
    server = _seed_existing_dashboard(install_dir)
    original = server.read_bytes()
    manifest = install_dir / "install-state.json"
    manifest_before = manifest.read_text(encoding="utf-8")
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
    assert "server_core.py" not in result.stdout
    assert server.read_bytes() == original
    assert manifest.read_text(encoding="utf-8") == manifest_before


def test_provider_installer_keeps_existing_server_byte_for_byte(tmp_path):
    install_dir = tmp_path / "agentstack"
    original = "# installed dashboard from current ORRERY version\nSENTINEL = 42\n"
    server = _seed_existing_dashboard(install_dir, original)

    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert server.read_text(encoding="utf-8") == original
    assert not (install_dir / "dashboard" / "server_core.py").exists()
    assert (install_dir / "dashboard" / "provider_server.py").is_file()


def test_provider_installer_records_added_files_in_core_manifest(tmp_path):
    install_dir = tmp_path / "agentstack"
    server = _seed_existing_dashboard(install_dir)
    original_owned = {str(server)}
    result = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((install_dir / "install-state.json").read_text(encoding="utf-8"))
    owned = set(manifest["owned_files"])
    assert original_owned <= owned
    for relative in EXPECTED_PAYLOAD:
        assert str(install_dir / relative) in owned, relative
    assert str(install_dir / "dashboard" / "server_core.py") not in owned


def test_provider_installer_copies_dashboard_abstraction_and_adapter(tmp_path):
    install_dir = tmp_path / "agentstack"
    _seed_existing_dashboard(install_dir)
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
    assert os.access(install_dir / "dashboard" / "provider_server.py", os.X_OK)
    assert os.access(install_dir / "dashboard" / "service_runner.py", os.X_OK)
    assert not os.access(install_dir / "dashboard" / "assets" / "google.svg", os.X_OK)


def test_provider_installer_is_idempotent_without_rewriting_core(tmp_path):
    install_dir = tmp_path / "agentstack"
    original = "# original core\nSENTINEL = 'keep-me'\n"
    server = _seed_existing_dashboard(install_dir, original)
    first = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_manifest = json.loads((install_dir / "install-state.json").read_text(encoding="utf-8"))
    second = subprocess.run(
        ["bash", str(INSTALLER), "--install-dir", str(install_dir)],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert server.read_text(encoding="utf-8") == original
    second_manifest = json.loads((install_dir / "install-state.json").read_text(encoding="utf-8"))
    assert second_manifest["owned_files"] == first_manifest["owned_files"]


def test_provider_installer_does_not_copy_obsolete_dashboard_wrappers(tmp_path):
    install_dir = tmp_path / "agentstack"
    _seed_existing_dashboard(install_dir)
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
    assert not (install_dir / "dashboard" / "server_core.py").exists()
