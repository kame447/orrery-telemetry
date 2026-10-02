"""Persistent launch workspace boundaries without real providers or Mail."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from test_persistent_agents import (
    _fixture,
    _init_linked_worktree,
    _install_claude_fixture_plugin,
    _write_executable,
)


ROOT = Path(__file__).resolve().parents[1]
STALE_KEYS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
    "AGENTSTACK_PROJECT_CONTEXT", "AGENTSTACK_LOOKUP_PROJECT_KEY",
)


@pytest.fixture(autouse=True)
def isolated_git_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "git-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for name in STALE_KEYS[:3]:
        monkeypatch.delenv(name, raising=False)


class Launch:
    def __init__(self, root: Path, provider: str = "codex", interaction: str = "interactive"):
        self.root = root
        self.record = root / "provider.json"
        self.enrolled = root / "enroll-inspected"
        name = provider if interaction == "interactive" else "fixture-bridge"
        self.command = _write_executable(
            root / "commands" / name,
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            f"pathlib.Path({str(self.record)!r}).write_text(json.dumps({{"
            "'cwd': os.getcwd(), 'argv': sys.argv[1:], 'environment': dict(os.environ)}))\n",
        )
        argv = [str(self.command), "literal argument; $(never-run)"]
        if provider == "claude" and interaction == "interactive":
            argv.extend(["--channels", "plugin:dummy-channel@fixture"])
        self.profile, self.runtime, self.state, inherited = _fixture(
            root, interaction=interaction, provider=provider, command=argv
        )
        home = root / "home"
        home.mkdir()
        self.env = {
            key: value for key, value in inherited.items() if key.startswith("AGENTSTACK_")
        }
        self.env.update({
            "HOME": str(home), "PATH": os.environ.get("PATH", os.defpath),
            "TMPDIR": str(root), "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
            "AGENTSTACK_EXTRA_PROTECTED_ROOTS": "",
            "PARENT_AGENT": "InheritedParent", "CHILD_REGISTRATION_TOKEN": "fixture-stale",
            "AGENTSTACK_RESERVED_IDENTITY": "1", "AGENT_NAME": "InheritedName",
            "AGENTSTACK_CODEX_LAUNCH_BINDING": "/stale/binding",
            "AGENTSTACK_CODEX_LAUNCH_ID": "stale-launch-id",
            "AGENTSTACK_PROJECT_KEY": "ambient-namespace", "PROJECT_KEY": "ambient-alias",
            "AGENTSTACK_PROJECT_REPOSITORY": "/stale/repository",
            "AGENTSTACK_PROJECT_WORK_DIR": "/stale/work-dir",
            "AGENTSTACK_PROJECT_WORKTREE_ROOT": "/stale/worktree",
            "AGENTSTACK_PROTECTED_ROOTS": "/stale/protected-root",
            **{name: "/stale/value" for name in STALE_KEYS},
        })
        if provider == "claude" and interaction == "interactive":
            _install_claude_fixture_plugin(root, self.profile)
        self.install = Path(self.env["AGENTSTACK_HOME"])
        (self.install / "bin").mkdir()
        (self.install / "hooks").mkdir()
        self.launcher = self.install / "bin" / "agentstack-persistent"
        self.helper = self.install / "hooks" / "project-context.sh"
        shutil.copy2(ROOT / "bin" / "agentstack-persistent", self.launcher)
        shutil.copy2(ROOT / "hooks" / "project-context.sh", self.helper)
        override = root / "override-hooks"
        override.mkdir()
        self.override_marker = root / "override-helper-ran"
        _write_executable(
            override / "project-context.sh",
            "#!/bin/sh\n" + f"touch {shlex.quote(str(self.override_marker))}\nexit 91\n",
        )
        shutil.copy2(ROOT / "hooks" / "prepare-codex-session-binding.py", override)
        self.env["AGENTSTACK_HOOKS_DIR"] = str(override)
        enroll = Path(self.env["AGENTSTACK_ENROLL_BIN"])
        body = enroll.read_text()
        enroll.write_text(body.replace(
            "import json\n",
            "import json\nfrom pathlib import Path\n"
            f"Path({str(self.enrolled)!r}).write_text('inspect only')\n",
        ))
        self.credential = self.runtime / "agent_token_PersistentBot"
        self.credential.write_text("fixture-existing-credential\n")
        self.credential.chmod(0o600)
        # Keep the wrapper's ancillary OS surfaces inside this test directory.
        # The real launch path, context helper, state, overlay and exec remain.
        self.harness = root / "harness.py"
        self.harness.write_text(
            "import os, pathlib, runpy, sys\n"
            f"namespace = runpy.run_path({str(self.launcher)!r})\n"
            # Sandboxes can synthesize an empty /tmp/.git outside the fixture.
            # Keep existing Claude ancestor discovery inside this isolated
            # filesystem model without changing production discovery behavior.
            "real_lexists = os.path.lexists\n"
            "def isolated_lexists(path):\n"
            "    candidate = pathlib.Path(path)\n"
            f"    if os.environ.get('TEST_PLAIN_WORKSPACE') and candidate.name == '.git' and not candidate.is_relative_to({str(root)!r}):\n"
            "        return False\n"
            "    return real_lexists(path)\n"
            "os.path.lexists = isolated_lexists\n"
            "namespace['main'].__globals__['_reject_managed_claude_config'] = lambda: None\n"
            "namespace['main'].__globals__['_wake_socket'] = "
            f"lambda *args: pathlib.Path({str(root / 'wake.sock')!r})\n"
            "namespace['main'](sys.argv[1:])\n"
        )

    def value(self):
        return json.loads(self.profile.read_text())

    def update(self, **fields):
        value = self.value()
        value.update(fields)
        self.profile.write_text(json.dumps(value))

    def run(self):
        return subprocess.run(
            [sys.executable, str(self.harness), "run", "--profile", str(self.profile)],
            env=self.env, cwd=self.root, text=True, capture_output=True, timeout=20,
        )

    def snapshot(self):
        result = self.run()
        assert result.returncode == 0, result.stderr
        assert self.enrolled.exists()
        assert not self.override_marker.exists()
        assert self.credential.read_text() == "fixture-existing-credential\n"
        return json.loads(self.record.read_text())

    def assert_no_launch_side_effects(self):
        assert not self.enrolled.exists()
        assert not self.state.exists()
        assert not self.record.exists()
        assert not (self.runtime / "persistent").exists()
        assert not (self.runtime / "codex_launches").exists()
        assert self.credential.read_text() == "fixture-existing-credential\n"


@pytest.mark.parametrize("provider", ("claude", "codex"))
@pytest.mark.parametrize("interaction", ("interactive", "headless"))
@pytest.mark.parametrize("workspace", ("git", "linked", "plain"))
def test_persistent_launch_uses_declared_workspace_not_identity_or_ambient_roots(
    tmp_path: Path, provider: str, interaction: str, workspace: str,
):
    launch = Launch(tmp_path, provider, interaction)
    main = tmp_path / "main repository"
    linked = tmp_path / "linked workspace"
    _init_linked_worktree(main, linked)
    workspace_root = {"git": main, "linked": linked, "plain": tmp_path / "plain workspace"}[workspace]
    workdir = workspace_root / "nested 'directory'"
    workdir.mkdir(parents=True)
    if workspace == "plain":
        launch.env["TEST_PLAIN_WORKSPACE"] = "1"
    launch.update(working_directory=str(workdir))
    snapshot = launch.snapshot()
    env = snapshot["environment"]
    assert snapshot["cwd"] == str(workdir)
    assert snapshot["argv"][0] == "literal argument; $(never-run)"
    assert env["AGENTSTACK_PROJECT_KEY"] == env["PROJECT_KEY"] == launch.value()["project_key"]
    assert env["AGENTSTACK_PROJECT_REPOSITORY"] == ("" if workspace == "plain" else str(main))
    assert env["AGENTSTACK_PROJECT_WORKTREE_ROOT"] == ("" if workspace == "plain" else str(workspace_root))
    assert env["AGENTSTACK_PROJECT_WORK_DIR"] == str(workdir)
    assert env["AGENTSTACK_PROTECTED_ROOTS"] == str(workdir if workspace == "plain" else workspace_root)
    assert env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] == ""
    assert env["AGENTSTACK_PROTECTION_CONTEXT"] == "workspace-v1"
    assert not set(STALE_KEYS) & env.keys()
    assert env["AGENT_NAME"] == "PersistentBot"
    assert not {"PARENT_AGENT", "CHILD_REGISTRATION_TOKEN", "AGENTSTACK_RESERVED_IDENTITY"} & env.keys()
    assert env["AGENTSTACK_PERSISTENT_STATE_DIR"] == str(launch.state)
    assert env["AGENTSTACK_PERSISTENT_PARENTLESS"] == "1"
    manifest = json.loads(Path(env["AGENTSTACK_PERSISTENT_RUNTIME_MANIFEST"]).read_text())
    assert manifest["agent_id"] == 41
    assert manifest["project_key"] == launch.value()["project_key"]
    if provider == "codex":
        assert env["CODEX_HOME"] == str(launch.state / "codex-home")
        assert env["CODEX_SHARED_CODEX_DIR"] == env["CODEX_HOME"]
        assert env["AGENTSTACK_CODEX_LAUNCH_ID"] != "stale-launch-id"
        assert Path(env["AGENTSTACK_CODEX_LAUNCH_BINDING"]).is_file()
    elif interaction == "interactive":
        assert snapshot["argv"][1:3] == ["--channels", "plugin:dummy-channel@fixture"]
        assert snapshot["argv"][3:] == ["--mcp-config", env["AGENTSTACK_PERSISTENT_MCP_CONFIG"]]
    if interaction == "headless":
        assert env["AGENTSTACK_PERSISTENT_WAKE_SOCKET"] == str(tmp_path / "wake.sock")


@pytest.mark.parametrize("selection", ("profile", "profile-empty", "live", "live-empty", "installed", "none"))
def test_persistent_extras_presence_precedence_and_order(tmp_path: Path, selection: str):
    launch = Launch(tmp_path)
    shared = tmp_path / "shared 'root'"
    shared.mkdir()
    alias = tmp_path / "shared alias"
    alias.symlink_to(shared, target_is_directory=True)
    installed = tmp_path / "installed extra"
    installed.mkdir()
    (launch.install / "env.sh").write_text(
        f"export AGENTSTACK_EXTRA_PROTECTED_ROOTS={shlex.quote(str(installed))}\n"
        "export AGENTSTACK_PROTECTED_ROOTS='/obsolete/installed-root'\n"
    )
    workdir = Path(launch.value()["working_directory"])
    explicit = f"{alias}/:{shared}:{workdir}"
    launch.env.pop("AGENTSTACK_EXTRA_PROTECTED_ROOTS")
    expected = []
    if selection.startswith("profile"):
        launch.env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = str(installed)
        launch.update(environment={
            "AGENTSTACK_EXTRA_PROTECTED_ROOTS": explicit if selection == "profile" else "",
            "AGENTSTACK_PROJECT_KEY": "wrong-profile-namespace",
            "AGENTSTACK_PROTECTED_ROOTS": "/obsolete/profile-root",
        })
        expected = [str(shared), str(workdir)] if selection == "profile" else []
    elif selection.startswith("live"):
        launch.env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] = explicit if selection == "live" else ""
        expected = [str(shared), str(workdir)] if selection == "live" else []
    elif selection == "installed":
        expected = [str(installed)]
    else:
        (launch.install / "env.sh").write_text("export AGENTSTACK_PROTECTED_ROOTS='/obsolete/installed-root'\n")
    snapshot = launch.snapshot()
    env = snapshot["environment"]
    assert env["AGENTSTACK_EXTRA_PROTECTED_ROOTS"] == ":".join(expected)
    assert env["AGENTSTACK_PROTECTED_ROOTS"] == ":".join(dict.fromkeys([*expected, str(workdir)]))
    assert env["AGENTSTACK_PROJECT_KEY"] == launch.value()["project_key"]
    manifest = json.loads(Path(env["AGENTSTACK_PERSISTENT_RUNTIME_MANIFEST"]).read_text())
    if selection == "none":
        assert "legacy AGENTSTACK_PROTECTED_ROOTS ignored" in manifest["workspace_warning"]
    else:
        assert "workspace_warning" not in manifest


def test_persistent_headless_resolves_symlink_to_physical_workspace(tmp_path: Path):
    launch = Launch(tmp_path, "claude", "headless")
    actual = Path(launch.value()["working_directory"])
    alias = tmp_path / "workspace alias"
    alias.symlink_to(actual, target_is_directory=True)
    launch.update(working_directory=str(alias))
    snapshot = launch.snapshot()
    assert snapshot["cwd"] == str(actual)
    assert snapshot["environment"]["AGENTSTACK_PROTECTED_ROOTS"] == str(actual)


def test_persistent_interactive_claude_retains_symlink_inspection_rejection(tmp_path: Path):
    launch = Launch(tmp_path, "claude", "interactive")
    alias = tmp_path / "workspace alias"
    alias.symlink_to(Path(launch.value()["working_directory"]), target_is_directory=True)
    launch.update(working_directory=str(alias))
    result = launch.run()
    assert result.returncode == 2
    error = json.loads(result.stderr)
    assert error["reason"] == "claude-project-root-unsupported"
    assert error["path"] == str(alias)
    assert not launch.record.exists()
    assert not (launch.state / "claude-mcp.json").exists()
    assert not (launch.runtime / "persistent").exists()


@pytest.mark.parametrize("rejection", ("enrollment", "command", "exec"))
def test_legacy_root_warning_does_not_corrupt_later_json_error(tmp_path: Path, rejection: str):
    launch = Launch(tmp_path)
    launch.env.pop("AGENTSTACK_EXTRA_PROTECTED_ROOTS")
    if rejection == "enrollment":
        enroll = Path(launch.env["AGENTSTACK_ENROLL_BIN"])
        source = enroll.read_text()
        assert "'local_credential_state': 'present'" in source
        enroll.write_text(source.replace("'local_credential_state': 'present'", "'local_credential_state': 'missing'"))
        expected = "local-credential-unavailable"
    elif rejection == "command":
        launch.command.unlink()
        expected = "profile-command-unavailable"
    else:
        launch.command.write_text("#!/nonexistent/fixture-interpreter\n")
        expected = "persistent-launch-failed"
    result = launch.run()
    assert result.returncode == 2
    assert json.loads(result.stderr)["reason"] == expected
    assert not launch.record.exists()
    assert launch.credential.read_text() == "fixture-existing-credential\n"


@pytest.mark.parametrize("failure", ("missing-helper", "missing-workspace", "missing-git", "broken-git", "relative-extra", "newline-extra", "colon-workspace", "malformed-json", "wrong-namespace"))
def test_invalid_workspace_fails_before_enrollment_or_state(tmp_path: Path, failure: str):
    launch = Launch(tmp_path)
    if failure == "missing-helper":
        launch.helper.unlink()
    elif failure == "missing-workspace":
        launch.update(working_directory=str(tmp_path / "does-not-exist"))
    elif failure == "missing-git":
        launch.update(environment={"PATH": str(tmp_path / "empty-bin")})
    elif failure == "broken-git":
        workdir = Path(launch.value()["working_directory"])
        (workdir / ".git").write_text("gitdir: /nonexistent/fixture-git-dir\n")
    elif failure in {"relative-extra", "newline-extra"}:
        launch.update(environment={"AGENTSTACK_EXTRA_PROTECTED_ROOTS": "relative/root" if failure == "relative-extra" else "/extra\nroot"})
    elif failure == "colon-workspace":
        workdir = tmp_path / "colon:workspace"
        workdir.mkdir()
        launch.update(working_directory=str(workdir))
    elif failure == "malformed-json":
        launch.helper.write_text("#!/bin/bash\nprintf 'not-json'\n")
    else:
        launch.helper.write_text(
            "#!/bin/bash\n"
            f"/bin/bash {shlex.quote(str(ROOT / 'hooks/project-context.sh'))} workspace-context-json \"$2\" wrong-namespace\n"
        )
    result = launch.run()
    assert result.returncode == 2
    assert json.loads(result.stderr)["reason"] in {
        "workspace-context-helper-unavailable", "working-directory-unavailable",
        "workspace-context-unavailable", "workspace-context-invalid",
    }
    launch.assert_no_launch_side_effects()
