from __future__ import annotations

import threading
import time

import dashboard.provider_server as server
from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
)


def test_async_adapter_status_keeps_actual_provider_metadata(monkeypatch, tmp_path):
    future = ProviderSpec(
        id="future-ai",
        label="Future AI",
        program="future-cli",
        models=("future-1",),
        default_model="future-1",
        capabilities=ProviderCapabilities(
            effort=False,
            mcp=True,
            resume=False,
            runtime=True,
            transcript=False,
            standalone=True,
            worktree_required=False,
            resources_required=False,
        ),
        provider_key="future-vendor",
        adapter_script="spawn-future.sh",
    )
    monkeypatch.setattr(server, "PROVIDER_REGISTRY", ProviderRegistry([future]))

    launcher = tmp_path / "spawn-future.sh"
    launcher.write_text("#!/bin/bash\n", encoding="utf-8")
    launcher.chmod(0o755)
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    monkeypatch.setattr(server, "HOOKS_DIR", str(tmp_path))
    monkeypatch.setattr(server, "SPAWN_SCRIPT", str(tmp_path / "native-spawn.sh"))
    monkeypatch.setattr(server, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(server, "HERE", str(tmp_path))
    monkeypatch.setattr(server, "_project_key", lambda: "/project")
    monkeypatch.setattr(server, "_spawn_name_status", lambda _name: "available")
    monkeypatch.setattr(
        server,
        "_mcp_call",
        lambda method, args, timeout=15: {
            "ok": True,
            "data": {"name": "FutureCurie", "registration_token": "tok"}
            if method == "register_agent" else {},
        },
    )

    release = threading.Event()

    class SlowProc:
        def wait(self, timeout=None):
            assert release.wait(timeout=5), "launcher wait was never released"
            return 0

    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: SlowProc())
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )

    pending = server.do_spawn({
        "standalone": True,
        "async": True,
        "name": "FutureCurie",
        "task": "work",
        "dir": str(tmp_path),
        "provider": "future-ai",
        "model": "future-1",
    })
    assert pending["ok"] is True and pending["pending"] is True
    assert pending["provider"] == "future-ai"

    release.set()
    deadline = time.monotonic() + 5
    status = server.spawn_launch_status("FutureCurie")
    while status["state"] == "launching" and time.monotonic() < deadline:
        time.sleep(0.02)
        status = server.spawn_launch_status("FutureCurie")

    assert status["state"] == "ready", status
    assert status["result"]["provider"] == "future-ai"
    assert status["result"]["model"] == "future-1"
    assert status["result"]["effort"] is None
