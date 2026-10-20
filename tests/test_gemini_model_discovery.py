"""Antigravity CLI model discovery must not change the explicit spawn policy."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

import dashboard.provider_server  # noqa: F401 - loads the optional Gemini provider


gemini = sys.modules["_orrery_provider_dashboard_gemini"]
BASE = SimpleNamespace(_SPAWN_MODELS=(), _codex_models=lambda: ())
FALLBACK = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]


def _fake_cli(monkeypatch, tmp_path, body: str):
    binary = tmp_path / "agy"
    binary.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("AGENTSTACK_GEMINI_BIN", str(binary))
    monkeypatch.delenv("AGENTSTACK_GEMINI_MODELS", raising=False)
    return binary


def test_cli_adds_argon_without_mixing_claude_or_gpt(monkeypatch, tmp_path):
    rows = (
        "gemini-4-argon-medium\tArgon Medium\n"
        "gemini-4-argon-high\tArgon High\n"
        "gemini-4-argon-low\tArgon Low\n"
        "gemini-3.8-flash-high\tFlash High\n"
        "gemini-4-argon-high\tDuplicate\n"
        "claude-opus-5-5-high\tClaude\n"
        "gpt-oss-120b-medium\tGPT\n"
        "gemini-invalid model\tBad\n"
    )
    _fake_cli(monkeypatch, tmp_path, "cat <<'EOF'\n" + rows + "EOF")
    models, error = gemini._gemini_models(BASE)
    assert error == ""
    assert models == ["gemini-3.8-flash-high", "gemini-4-argon-medium",
                      "gemini-4-argon-high", "gemini-4-argon-low"]


def test_cli_list_is_cached_and_override_skips_discovery(monkeypatch, tmp_path):
    counter = tmp_path / "calls.txt"
    monkeypatch.setenv("AGY_TEST_COUNTER", str(counter))
    _fake_cli(monkeypatch, tmp_path, "echo call >> \"$AGY_TEST_COUNTER\"\n"
              "printf 'gemini-4-argon-high\\tArgon High\\n'")
    assert gemini._gemini_models(BASE)[0] == ["gemini-4-argon-high"]
    assert gemini._gemini_models(BASE)[0] == ["gemini-4-argon-high"]
    assert counter.read_text().splitlines() == ["call"]

    monkeypatch.setenv("AGENTSTACK_GEMINI_MODELS", "gemini-custom-high")
    assert gemini._gemini_models(BASE) == (["gemini-custom-high"], "")
    assert counter.read_text().splitlines() == ["call"]


@pytest.mark.parametrize("returncode, output", [
    (1, b"gemini-4-argon-high\tArgon\n"),
    (0, b"claude-opus-5-5-high\tClaude\n"),
    (0, b"gemini-4-argon-high no-tab\n"),
    (0, b"gemini-4-argon-high\tArgon\n" * 6000),
    (0, b"".join(f"gemini-test-{i}\tTest\n".encode() for i in range(129))),
    (0, b"\xff\xfe"),
])
def test_cli_unusable_response_falls_back(monkeypatch, tmp_path, returncode, output):
    _fake_cli(monkeypatch, tmp_path, "exit 0")
    gemini._cli_models.cache_clear()
    monkeypatch.setattr(gemini.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], returncode, output))
    assert gemini._gemini_models(BASE) == (FALLBACK, "")


def test_cli_timeout_falls_back(monkeypatch, tmp_path):
    _fake_cli(monkeypatch, tmp_path, "exit 0")
    gemini._cli_models.cache_clear()

    def timeout(*args, **kwargs):
        assert args[0][-1] == "models"
        assert kwargs["timeout"] == gemini._CLI_CATALOG_TIMEOUT
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(gemini.subprocess, "run", timeout)
    assert gemini._gemini_models(BASE) == (FALLBACK, "")
