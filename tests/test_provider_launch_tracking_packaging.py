from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_gemini_provider_installer_packages_async_provider_tracking():
    text = (ROOT / "scripts" / "install-gemini-provider.sh").read_text(encoding="utf-8")
    assert '"dashboard/provider_launch_tracking.py"' in text
