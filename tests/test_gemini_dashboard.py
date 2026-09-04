"""Dashboard coverage for capability-driven Antigravity integration."""
from __future__ import annotations

import pathlib

import dashboard.server as server


ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_spawn_catalog_exposes_gemini_provider(monkeypatch):
    monkeypatch.setattr(
        server,
        "_spawn_scientist_statuses",
        lambda _adjectives, scientists: {name: "unknown" for name in scientists},
    )
    data = server.spawn_names_payload()
    gemini = next(provider for provider in data["providers"] if provider["id"] == "gemini")
    assert gemini["program"] == "antigravity"
    assert gemini["default_model"] == "gemini-3.8-flash-high"
    assert gemini["efforts"] == ["low", "medium", "high"]
    assert gemini["capabilities"]["resources_required"] is True
    assert gemini["capabilities"]["worktree_required"] is True


def test_rendered_dashboard_uses_capabilities_not_gemini_name_checks():
    source = (ROOT / "dashboard" / "index.html").read_bytes()
    rendered = server._render_dashboard_index(source).decode("utf-8")
    assert "a.provider&&_PROVIDER_ASPECT[a.provider]" in rendered
    assert "google: 1" in rendered
    assert 'id="spm-resources-row"' in rendered
    assert 'id="spm-resources"' in rendered
    assert "providerCaps.resources_required" in rendered
    assert "providerCaps.worktree_required" in rendered
    assert "payload.resources=resources" in rendered
    assert "spmSelectedProvider==='gemini'" not in rendered


def test_google_badge_asset_is_local():
    asset = ROOT / "dashboard" / "assets" / "google.svg"
    assert asset.is_file()
    text = asset.read_text(encoding="utf-8")
    assert "<svg" in text and "#ece2cc" in text


def test_gemini_dashboard_adapter_is_shell_parseable():
    import subprocess

    adapter = ROOT / "hooks" / "spawn_gemini_preregistered.sh"
    result = subprocess.run(
        ["bash", "-n", str(adapter)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_gemini_spawn_requires_declared_resources(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_suggest_any_spawn_name", lambda: "Calm-Curie")
    payload = {
        "parent": "Parent-Curie",
        "task": "inspect the dashboard",
        "dir": str(tmp_path),
        "provider": "gemini",
        "model": "gemini-3.8-flash-high",
        "effort": "high",
    }
    result = server.do_spawn(payload)
    assert result == {
        "ok": False,
        "error": "resources required for provider gemini",
    }


def test_provider_resume_capability_blocks_wrong_fallback(monkeypatch):
    monkeypatch.setattr(server, "_agent_program", lambda _name: "antigravity")
    result = server.do_resume("Calm-Curie")
    assert result["ok"] is False
    assert result["error"].startswith("resume not supported for provider gemini")
    assert "another provider" in result["error"]
