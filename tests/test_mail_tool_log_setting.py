"""Mail tool logging follows the installer's persisted setting lifecycle."""
from __future__ import annotations

import os
import stat
import subprocess

import pytest

from test_setting_precedence import (
    BASH, CONTEXT, INSTALLER, _fake_bin, _previous_install,
    _resolve_like_the_installer, _scrubbed_env, _write_env_sh_like_the_installer,
)


KEY = "AGENTSTACK_MAIL_TOOLS_LOG_ENABLED"
VARIABLE = "MAIL_TOOLS_LOG_ENABLED_SETTING"


def previous_logging_install(tmp_path, value="false", *, chosen=True):
    home, values = _previous_install(tmp_path, **{VARIABLE: value})
    if chosen:
        values["CHOSEN_SETTINGS"] += " " + KEY
        _write_env_sh_like_the_installer(home, values)
    return home, values


def resolved(home, *, reset=False, env=None):
    arguments = ("--dashboard-only", "--reset-settings") if reset else ("--dashboard-only",)
    return _resolve_like_the_installer(home, VARIABLE, KEY, "CHOSEN_SETTINGS", args=arguments, env=env)


def test_disabled_tool_logging_is_written_and_survives_reinstall(tmp_path):
    home, values = previous_logging_install(tmp_path)
    env_file = home / ".agentstack/env.sh"
    loaded = subprocess.run(
        [BASH, "-c", '. "$1"; printf "%s" "$AGENTSTACK_MAIL_TOOLS_LOG_ENABLED"', "fixture", str(env_file)],
        env=_scrubbed_env(home), capture_output=True, text=True, check=True,
    )
    assert loaded.stdout == "false"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    value, exported, choices = resolved(home)
    assert value == exported == "false" and KEY in choices.split()
    values.update({VARIABLE: value, "CHOSEN_SETTINGS": choices})
    _write_env_sh_like_the_installer(home, values)
    assert resolved(home)[:2] == ["false", "false"]


def test_logging_default_is_true_and_not_an_implicit_choice(tmp_path):
    home, _ = previous_logging_install(tmp_path, "true", chosen=False)
    value, exported, choices = resolved(home)
    assert value == exported == "true"
    assert KEY not in choices.split()
    value, exported, choices = resolved(home, env={KEY: "false"})
    assert value == exported == "false" and KEY in choices.split()


def test_explicit_override_clear_and_reset_follow_existing_precedence(tmp_path):
    home, _ = previous_logging_install(tmp_path)
    assert resolved(home, env={KEY: "true"})[:2] == ["true", "true"]
    assert resolved(home, env={KEY: ""})[:2] == ["true", "true"]
    assert resolved(home, reset=True)[:2] == ["true", "true"]
    # A sourced copy of env.sh is an echo, not a new request that defeats reset.
    assert resolved(home, reset=True, env={KEY: "false"})[:2] == ["true", "true"]


def test_launcher_preserves_explicit_logging_override(tmp_path):
    home, _ = previous_logging_install(tmp_path, "true")
    env_file = home / ".agentstack/env.sh"
    loaded = subprocess.run(
        [BASH, "-c", '. "$1"; agentstack_load_installed_env "$2"; '
         'printf "%s" "$AGENTSTACK_MAIL_TOOLS_LOG_ENABLED"', "fixture", str(CONTEXT), str(env_file)],
        env=_scrubbed_env(home, **{KEY: "false"}), capture_output=True, text=True, check=True,
    )
    assert loaded.stdout == "false"


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("value", ["0", "False", "false\ntrue", 'false";__import__("sys").exit(0);#'])
def test_invalid_logging_value_stops_before_installed_files_change(tmp_path, dry_run, value):
    home, _ = previous_logging_install(tmp_path)
    env_file = home / ".agentstack/env.sh"
    before = env_file.read_bytes()
    env = _scrubbed_env(home, **{KEY: value})
    env["PATH"] = str(_fake_bin(tmp_path / "probe")) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [BASH, str(INSTALLER), "--dashboard-only", *(["--dry-run"] if dry_run else [])],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert KEY + " must be true or false" in result.stderr
    assert env_file.read_bytes() == before
    assert "[plan] write " not in result.stdout
    assert not (home / ".agentstack/install-state.json").exists()


def test_invalid_saved_logging_value_is_not_silently_reset(tmp_path):
    home, _ = previous_logging_install(tmp_path, "invalid")
    env_file = home / ".agentstack/env.sh"
    before = env_file.read_bytes()
    result = subprocess.run(
        [BASH, str(INSTALLER), "--dashboard-only", "--dry-run"],
        cwd=tmp_path, env=_scrubbed_env(home), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert KEY + " must be true or false" in result.stderr
    assert env_file.read_bytes() == before
