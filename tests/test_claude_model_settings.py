"""New model/profile settings survive shell, XML and environment boundaries."""
from pathlib import Path
import os
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("AGENTSTACK_CLAUDE_MODELS", "CLAUDE_CONFIG_DIR")


@pytest.mark.parametrize("mode", ["inherit", "replace", "clear"])
def test_installer_preserves_replaces_or_explicitly_clears_settings(tmp_path, mode):
    text = (ROOT / "scripts/install.sh").read_text()
    start = text.index('if [[ -z "${AGENTSTACK_CLAUDE_MODELS+x}" ]]')
    end = text.index('\nHOOKS_DIR=', start)
    snippet = text[start:end]
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    if mode != "inherit":
        env.update({k: "replacement" if mode == "replace" else "" for k in KEYS})
    setup = r'''CLAUDE_MODELS_SETTING="${AGENTSTACK_CLAUDE_MODELS:-}"
CLAUDE_CONFIG_DIR_SETTING="${CLAUDE_CONFIG_DIR:-}"
CODEX_MODELS_SETTING="codex-fixture"
INSTALL_DIR="/unused-fixture"
agentstack_installed_env_value() { printf '%s' "old-$1"; }
'''
    command = setup + snippet + '\nprintf "%s\\n%s\\n" "$AGENTSTACK_CLAUDE_MODELS" "$AGENTSTACK_INSTALL_CLAUDE_CONFIG_DIR"'
    result = subprocess.run(["/bin/bash", "-eu", "-c", command], env=env, text=True, capture_output=True, check=True)
    expected = ["old-" + k for k in KEYS] if mode == "inherit" else [env[k] for k in KEYS]
    assert result.stdout.splitlines() == expected


def test_controller_xml_renderer_preserves_special_characters(tmp_path):
    text = (ROOT / "dashboard/agentctl.sh").read_text()
    start = text.index('sed_escape() {')
    end = text.index('\nrender_plist()', start)
    value = str(tmp_path / 'team & "quoted" <profile>')
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], "VALUE": value}
    result = subprocess.run(["/bin/bash", "-eu", "-c", text[start:end] + '\nxml_sed_escape "$VALUE"'], env=env, text=True, capture_output=True, check=True)
    import xml.etree.ElementTree as ET
    rendered = subprocess.run(["sed", "s|__VALUE__|" + result.stdout + "|g"], input="<string>__VALUE__</string>", text=True, capture_output=True, check=True)
    assert ET.fromstring(rendered.stdout).text == value


def test_new_settings_are_data_not_interpolated_python_source():
    text = (ROOT / "scripts/install.sh").read_text()
    for key, transport in ((KEYS[0], KEYS[0]), (KEYS[1], "AGENTSTACK_INSTALL_CLAUDE_CONFIG_DIR")):
        assert text.count(f'"{key}": os.environ.get("{transport}", "")') == 3
    for variable in ("CLAUDE_MODELS_SETTING", "CLAUDE_CONFIG_DIR_SETTING"):
        assert not re.search(r'": "\$' + variable + '"', text)


def test_controller_background_exports_selected_settings(tmp_path):
    text = (ROOT / "dashboard/agentctl.sh").read_text()
    start = text.index('export_background_env() {')
    end = text.index('\nstart_background()', start)
    command = text[start:end] + '\nCLAUDE_MODELS_SETTING="claude-test-9"\nCLAUDE_CONFIG_DIR_SETTING="/test/profile"\nexport_background_env\nprintf "%s\\n%s\\n" "$AGENTSTACK_CLAUDE_MODELS" "$CLAUDE_CONFIG_DIR"'
    result = subprocess.run(["/bin/bash", "-e", "-c", command], env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]}, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["claude-test-9", "/test/profile"]


@pytest.mark.parametrize("value", ["", '/test/team & "quoted" <profile>'])
@pytest.mark.parametrize("writer", ["installer", "controller"])
def test_real_plist_writers_escape_once_and_omit_empty_profile(tmp_path, value, writer):
    import plistlib
    if writer == "installer":
        text = (ROOT / "scripts/install.sh").read_text()
        body = text[text.index("render_launchd_plist() {"):text.index("render_systemd_unit() {")]
        command = "plan() { :; }\n" + body + "\nrender_launchd_plist"
    else:
        text = (ROOT / "dashboard/agentctl.sh").read_text()
        body = text[text.index("sed_escape() {"):text.index("background_pid() {")]
        command = body + "\nrender_plist"
    env = {key: "" for key in re.findall(r"\$([A-Z][A-Z0-9_]*)", body)}
    output = tmp_path / "Library/LaunchAgents/test.plist"
    env.update({"HOME": str(tmp_path), "PATH": os.environ["PATH"],
                "REPO_ROOT": str(ROOT), "LABEL": "test", "DRY_RUN": "false",
                "PYTHON_BIN": sys.executable, "PLIST_DST": str(output),
                "PLIST_TEMPLATE": str(ROOT / "dashboard/agentdashboard.plist.template"),
                "CLAUDE_CONFIG_DIR_SETTING": value,
                "AGENTSTACK_INSTALL_CLAUDE_CONFIG_DIR": value,
                "CLAUDE_MODELS_SETTING": "claude-test-9",
                "AGENTSTACK_CLAUDE_MODELS": "claude-test-9"})
    subprocess.run(["/bin/bash", "-eu", "-c", command], env=env, capture_output=True, text=True, check=True)
    values = plistlib.loads(output.read_bytes())["EnvironmentVariables"]
    assert values["AGENTSTACK_CLAUDE_MODELS"] == "claude-test-9"
    if value:
        assert values["CLAUDE_CONFIG_DIR"] == value
    else:
        assert "CLAUDE_CONFIG_DIR" not in values


def test_empty_profile_is_unset_in_background_controller(tmp_path):
    text = (ROOT / "dashboard/agentctl.sh").read_text()
    body = text[text.index("export_background_env() {"):text.index("start_background() {")]
    command = body + '\nCLAUDE_CONFIG_DIR_SETTING=""\nexport CLAUDE_CONFIG_DIR="/old/profile"\nexport_background_env\ntest -z "${CLAUDE_CONFIG_DIR+x}"'
    subprocess.run(["/bin/bash", "-e", "-c", command], env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]}, capture_output=True, text=True, check=True)


@pytest.mark.parametrize("path_index", [0, 1], ids=["pre-registered", "direct"])
@pytest.mark.parametrize("profile", ["", "/fixture/team & profile"])
def test_tmux_child_uses_selected_profile_not_server_environment(tmp_path, path_index, profile):
    import shlex
    import shutil
    import tempfile
    import time
    tmux = shutil.which("tmux")
    if not tmux:
        pytest.skip("tmux is required for the actual process boundary")
    lines = (ROOT / "hooks/spawn_child.sh").read_text().splitlines()
    commands = []
    for index, line in enumerate(lines):
        if '-e "CLAUDE_CHILD_MODEL=$CHILD_MODEL"' not in line:
            continue
        start = index
        while 'tmux new-session -d -s "$CHILD_NAME"' not in lines[start]:
            start -= 1
        end = index
        while lines[end].rstrip().endswith("\\"):
            end += 1
        commands.append("\n".join(lines[start:end + 1]))
    assert len(commands) == 2
    home = tmp_path / "home"
    bindir = home / ".local/bin"
    bindir.mkdir(parents=True)
    output = tmp_path / "selected-profile"
    fake = bindir / "claude"
    temporary_output = output.with_suffix(".tmp")
    fake.write_text(
        '#!/bin/bash\nprintf "%s\\n" "${CLAUDE_CONFIG_DIR-unset}" > '
        + shlex.quote(str(temporary_output)) + "\nmv "
        + shlex.quote(str(temporary_output)) + " " + shlex.quote(str(output)) + "\n"
    )
    fake.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "cleanup-child-agent.sh").write_text("exit 0\n")
    # A short, private socket path avoids macOS's Unix socket length limit.
    with tempfile.TemporaryDirectory(prefix="orrery-profile-", dir="/tmp") as socket_dir:
        socket = str(Path(socket_dir) / "tmux")
        def run(*args):
            return subprocess.run([tmux, "-S", socket, *args], capture_output=True, text=True, check=True)
        try:
            run("new-session", "-d", "-s", "fixture-server", "-c", str(tmp_path), "sleep 30")
            run("set-environment", "-g", "CLAUDE_CONFIG_DIR", "/stale/server-profile")
            env = {"HOME": str(home), "PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": profile,
                   "TEST_TMUX": tmux, "TEST_SOCKET": socket, "TEST_HOOKS": str(hooks),
                   "CHILD_NAME": "fixture-child", "WORK_DIR": str(tmp_path),
                   "CHILD_MODEL": "claude-future-9", "CHILD_MCP_CONFIG": "", "CHILD_SHELL": "/bin/bash"}
            setup = 'tmux() { "$TEST_TMUX" -S "$TEST_SOCKET" "$@"; }\nTMUX_ENV_ARGS=(-e "HOME=$HOME" -e "AGENTSTACK_HOOKS_DIR=$TEST_HOOKS")\n'
            subprocess.run(["/bin/bash", "-eu", "-c", setup + commands[path_index]], env=env, capture_output=True, text=True, check=True)
            deadline = time.monotonic() + 5
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert output.read_text().strip() == (profile or "unset")
        finally:
            subprocess.run([tmux, "-S", socket, "kill-server"], capture_output=True, timeout=5)


def test_explicit_profile_cannot_claim_a_prestarted_warm_session():
    text = (ROOT / "hooks/spawn_child.sh").read_text()
    assert 'if [[ "$STANDALONE" == true || -n "${CLAUDE_CONFIG_DIR:-}" ]]; then' in text
