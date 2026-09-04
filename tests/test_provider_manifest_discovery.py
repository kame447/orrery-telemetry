from __future__ import annotations

import json
import pathlib

import pytest

from dashboard.providers.registry import default_provider_registry


def _write_manifest(root: pathlib.Path, name: str, payload: dict) -> pathlib.Path:
    directory = root / "provider_specs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _future_manifest() -> dict:
    return {
        "id": "future-ai",
        "label": "Future AI",
        "program": "future-cli",
        "models": ["future-1"],
        "default_model": "future-1",
        "capabilities": {
            "effort": False,
            "mcp": True,
            "resume": False,
            "runtime": True,
            "transcript": False,
            "standalone": True,
            "worktree_required": False,
            "resources_required": False,
        },
        "provider_key": "future-vendor",
        "dispatch": "adapter",
        "adapter_script": "spawn_future.sh",
        "required_paths": ["hooks/spawn_future.sh"],
        "runtime_commands": ["future-cli"],
    }


def test_optional_provider_is_discovered_from_install_manifest(tmp_path):
    payload = _future_manifest()
    _write_manifest(tmp_path, "future-ai", payload)
    adapter = tmp_path / "hooks" / "spawn_future.sh"
    adapter.parent.mkdir(parents=True)
    adapter.write_text("#!/bin/sh\n", encoding="utf-8")

    registry = default_provider_registry(available_only=True, install_root=tmp_path)

    assert registry.ids() == ("claude", "codex", "future-ai")
    future = registry.require("future-ai")
    assert future.program == "future-cli"
    assert future.provider_key == "future-vendor"
    assert future.adapter_script == "spawn_future.sh"


def test_manifest_provider_is_hidden_until_required_payload_exists(tmp_path):
    _write_manifest(tmp_path, "future-ai", _future_manifest())
    registry = default_provider_registry(available_only=True, install_root=tmp_path)
    assert registry.ids() == ("claude", "codex")


def test_unknown_manifest_fields_fail_closed(tmp_path):
    payload = _future_manifest()
    payload["surprise_shell_command"] = "rm -rf /"
    _write_manifest(tmp_path, "future-ai", payload)

    with pytest.raises(ValueError, match="unknown provider manifest field"):
        default_provider_registry(install_root=tmp_path)


def test_manifest_cannot_override_builtin_provider_id(tmp_path):
    payload = _future_manifest()
    payload.update(id="claude", label="Fake Claude", program="evil-cli")
    _write_manifest(tmp_path, "fake-claude", payload)

    with pytest.raises(ValueError, match="provider already registered: claude"):
        default_provider_registry(install_root=tmp_path)


def test_source_registry_discovers_gemini_from_manifest_not_registry_literal():
    root = pathlib.Path(__file__).resolve().parent.parent
    manifest = root / "provider_specs" / "gemini.json"
    assert manifest.is_file()

    registry = default_provider_registry(install_root=root)
    gemini = registry.require("gemini")
    assert gemini.program == "antigravity"
    assert gemini.default_model == "gemini-3.8-flash-high"
