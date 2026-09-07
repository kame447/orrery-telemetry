from __future__ import annotations

import pathlib

from dashboard.providers.registry import default_provider_registry


ROOT = pathlib.Path(__file__).resolve().parent.parent


def _touch_executable(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def test_available_only_registry_hides_uninstalled_optional_provider(tmp_path):
    registry = default_provider_registry(available_only=True, install_root=tmp_path)
    assert registry.ids() == ("claude", "codex")


def test_available_only_registry_exposes_gemini_after_adapter_payload_exists(tmp_path):
    spec = tmp_path / "provider_specs" / "gemini.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_bytes((ROOT / "provider_specs" / "gemini.json").read_bytes())
    for relative in (
        "bin/agent-start-gemini",
        "bin/agentstack-gemini-child-mail",
        "bin/agentstack-gemini-stream",
        "hooks/spawn_gemini_preregistered.sh",
    ):
        _touch_executable(tmp_path / relative)

    registry = default_provider_registry(available_only=True, install_root=tmp_path)
    assert registry.ids() == ("claude", "codex", "gemini")


def test_source_registry_can_include_all_known_providers_without_install_probe():
    registry = default_provider_registry()
    assert registry.ids() == ("claude", "codex", "gemini")
