from __future__ import annotations

import pathlib
import sys

import dashboard.service_runner as service_runner


ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_provider_extension_does_not_replace_the_upstream_server_module() -> None:
    """Keep the large upstream-owned server.py stable for future upstream merges."""
    core_text = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
    assert "provider_runtime" not in core_text
    assert "server_core" not in core_text
    assert (ROOT / "dashboard" / "provider_server.py").is_file()
    assert not (ROOT / "dashboard" / "server_core.py").exists()


def test_service_runner_prefers_provider_entrypoint_when_present(monkeypatch, tmp_path):
    core = tmp_path / "server.py"
    provider = tmp_path / "provider_server.py"
    core.write_text("# core\n", encoding="utf-8")
    provider.write_text("# provider\n", encoding="utf-8")

    selected: list[pathlib.Path] = []
    monkeypatch.setattr(service_runner, "HERE", tmp_path)
    monkeypatch.setattr(service_runner, "run", lambda path: selected.append(path) or 0)
    monkeypatch.setattr(sys, "argv", ["service_runner.py"])

    assert service_runner.main() == 0
    assert selected == [provider]


def test_service_runner_keeps_explicit_server_path(monkeypatch, tmp_path):
    explicit = tmp_path / "custom-server.py"
    explicit.write_text("# explicit\n", encoding="utf-8")

    selected: list[pathlib.Path] = []
    monkeypatch.setattr(service_runner, "run", lambda path: selected.append(path) or 0)
    monkeypatch.setattr(sys, "argv", ["service_runner.py", str(explicit)])

    assert service_runner.main() == 0
    assert selected == [explicit]
