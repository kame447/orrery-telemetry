#!/usr/bin/env python3
"""Provider-aware dashboard entry point without modifying upstream server.py.

The upstream-owned dashboard server stays at ``dashboard/server.py`` so future
upstream changes can merge normally. This entry point loads an isolated copy of
that module, installs capability-driven provider behavior on the copy, and then
runs it. Importing this module from provider-specific tests returns the patched
copy rather than mutating ``dashboard.server`` globally.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys


_HERE = pathlib.Path(__file__).resolve().parent
_CORE_PATH = _HERE / "server.py"
_CORE_MODULE_NAME = "_orrery_provider_dashboard_core"


def _load_core():
    existing = sys.modules.get(_CORE_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_CORE_MODULE_NAME, _CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load dashboard core: {_CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CORE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_CORE_MODULE_NAME, None)
        raise
    return module


_base = _load_core()

try:
    from dashboard.provider_classification import install as _install_provider_classification
    from dashboard.provider_launch_tracking import install as _install_provider_launch_tracking
    from dashboard.provider_runtime import install as _install_provider_runtime
    from dashboard.providers.registry import default_provider_registry
except ModuleNotFoundError:  # direct `python dashboard/provider_server.py`
    from provider_classification import install as _install_provider_classification
    from provider_launch_tracking import install as _install_provider_launch_tracking
    from provider_runtime import install as _install_provider_runtime
    from providers.registry import default_provider_registry

_INSTALL_ROOT = _HERE.parent
_install_provider_runtime(
    _base,
    default_provider_registry(available_only=True, install_root=_INSTALL_ROOT),
)
_install_provider_classification(_base)
_install_provider_launch_tracking(_base)

if __name__ == "__main__":
    _base.main()
else:
    # Provider-specific callers get the isolated, patched core. The canonical
    # ``dashboard.server`` module remains untouched for upstream/core tests.
    sys.modules[__name__] = _base
