"""Static packaging contract for provider helpers in the normal installer."""
from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install.sh"


def test_main_installer_packages_gemini_bin_helpers():
    text = INSTALLER.read_text(encoding="utf-8")
    for name in (
        "agent-start-gemini",
        "agentstack-gemini-bootstrap",
        "agentstack-gemini-setup",
        "agentstack-gemini-child-mail",
        "agentstack-gemini-stream",
    ):
        assert f'$REPO_ROOT/bin/{name}' in text
        assert f'$BIN_DIR/{name}' in text


def test_main_installer_keeps_dashboard_registry_in_whole_tree_copy():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'copy_tree "$REPO_ROOT/dashboard" "$DASHBOARD_DIR"' in text


def test_main_installer_hook_copy_is_not_allowed_to_omit_provider_adapters():
    text = INSTALLER.read_text(encoding="utf-8")
    # The normal install must either copy the whole hooks tree or mention both
    # Gemini adapters explicitly. This allows the installer implementation to
    # stay generic while protecting the provider payload.
    copies_whole_hooks = (
        'copy_tree "$REPO_ROOT/hooks" "$HOOKS_DIR"' in text
        or 'sync_tree "$REPO_ROOT/hooks" "$HOOKS_DIR"' in text
    )
    explicit = all(
        name in text
        for name in ("spawn_gemini_child.sh", "spawn_gemini_preregistered.sh")
    )
    assert copies_whole_hooks or explicit
