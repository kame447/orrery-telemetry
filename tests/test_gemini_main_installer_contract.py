"""Packaging boundary between the provider core and optional adapters."""
from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install.sh"
GEMINI_INSTALLER = ROOT / "scripts" / "install-gemini-provider.sh"


def test_main_installer_copies_provider_core_with_dashboard_tree():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'copy_tree "$REPO_ROOT/dashboard" "$DASHBOARD_DIR"' in text
    # Provider-specific launchers stay out of the core helper whitelist. Adding
    # a future provider should not require another edit to this large installer.
    assert '$REPO_ROOT/bin/agent-start-gemini' not in text


def test_main_installer_copies_hooks_generically():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'copy_tree "$REPO_ROOT/hooks" "$HOOKS_DIR"' in text


def test_optional_provider_installer_owns_gemini_specific_payload():
    text = GEMINI_INSTALLER.read_text(encoding="utf-8")
    for name in (
        "agent-start-gemini",
        "agentstack-gemini-bootstrap",
        "agentstack-gemini-setup",
        "agentstack-gemini-child-mail",
        "agentstack-gemini-stream",
        "spawn_gemini_preregistered.sh",
    ):
        assert name in text
