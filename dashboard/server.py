#!/usr/bin/env python3
"""Dashboard entry point with capability-driven launch providers installed.

The legacy control-plane implementation lives in ``server_core.py`` unchanged.
Keeping it intact makes the provider refactor reversible and keeps existing
unit tests/monkeypatches pointed at the same function globals.
"""
from __future__ import annotations

import pathlib
import sys

try:
    from dashboard import server_core as _base
    from dashboard.provider_classification import install as _install_provider_classification
    from dashboard.provider_runtime import install as _install_provider_runtime
    from dashboard.providers.registry import default_provider_registry
except ModuleNotFoundError:  # direct `python dashboard/server.py`
    import server_core as _base
    from provider_classification import install as _install_provider_classification
    from provider_runtime import install as _install_provider_runtime
    from providers.registry import default_provider_registry

_INSTALL_ROOT = pathlib.Path(__file__).resolve().parent.parent
_install_provider_runtime(
    _base,
    default_provider_registry(available_only=True, install_root=_INSTALL_ROOT),
)
_install_provider_classification(_base)

if __name__ == "__main__":
    _base.main()
else:
    # Preserve `import dashboard.server as server` semantics for the existing
    # test suite: callers receive the actual core module after provider hooks
    # have been installed, so monkeypatching its globals still works.
    sys.modules[__name__] = _base
