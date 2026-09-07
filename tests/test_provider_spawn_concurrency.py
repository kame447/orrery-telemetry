from __future__ import annotations

import threading
from types import SimpleNamespace

from dashboard import provider_runtime
from dashboard.providers.registry import (
    ProviderCapabilities,
    ProviderRegistry,
    ProviderSpec,
)


def _provider(
    provider_id: str,
    *,
    program: str,
    model: str,
    dispatch: str,
    adapter_script: str = "",
) -> ProviderSpec:
    return ProviderSpec(
        id=provider_id,
        label=provider_id,
        program=program,
        models=(model,),
        default_model=model,
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
        provider_key=provider_id,
        dispatch=dispatch,
        adapter_script=adapter_script,
    )


def test_native_spawn_waits_while_adapter_temporarily_patches_legacy_globals(tmp_path):
    adapter = tmp_path / "adapter.sh"
    adapter.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    adapter.chmod(0o755)

    registry = ProviderRegistry(
        [
            _provider(
                "native-ai",
                program="native-cli",
                model="native-model",
                dispatch="native",
            ),
            _provider(
                "adapter-ai",
                program="adapter-cli",
                model="adapter-model",
                dispatch="adapter",
                adapter_script=adapter.name,
            ),
        ]
    )

    adapter_entered = threading.Event()
    release_adapter = threading.Event()
    native_entered = threading.Event()
    observations: dict[str, str] = {}

    base = SimpleNamespace(
        PROVIDER_REGISTRY=registry,
        RUNTIME_DIR=str(tmp_path / "runtime"),
        HOOKS_DIR=str(tmp_path),
        SPAWN_SCRIPT=str(tmp_path / "native-spawn.sh"),
        _SPAWN_MODELS={},
    )

    def original(payload: dict) -> dict:
        task = payload["task"]
        observations[task] = base.SPAWN_SCRIPT
        if task == "adapter":
            adapter_entered.set()
            assert release_adapter.wait(2)
        else:
            native_entered.set()
        return {
            "ok": True,
            "child_name": "AdapterCurie" if task == "adapter" else "NativeCurie",
        }

    base.do_spawn = original
    provider_runtime._install_spawn(base)

    adapter_result: dict = {}
    native_result: dict = {}

    adapter_thread = threading.Thread(
        target=lambda: adapter_result.update(
            base.do_spawn(
                {
                    "provider": "adapter-ai",
                    "model": "adapter-model",
                    "task": "adapter",
                    "standalone": True,
                }
            )
        )
    )
    adapter_thread.start()
    assert adapter_entered.wait(2)

    native_thread = threading.Thread(
        target=lambda: native_result.update(
            base.do_spawn(
                {
                    "provider": "native-ai",
                    "model": "native-model",
                    "task": "native",
                    "standalone": True,
                }
            )
        )
    )
    native_thread.start()

    # The native call must not enter the legacy core while SPAWN_SCRIPT points
    # at the adapter wrapper.
    assert not native_entered.wait(0.1)
    release_adapter.set()
    adapter_thread.join(2)
    native_thread.join(2)

    assert adapter_result["ok"] is True
    assert native_result["ok"] is True
    assert native_entered.is_set()
    assert observations["native"] == str(tmp_path / "native-spawn.sh")
