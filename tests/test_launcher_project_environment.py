"""Project-key precedence and tmux handoff, without live Mail or model calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = ("claude", "codex", "gemini")
LAUNCHERS = {
    "claude": "agent-start",
    "codex": "agent-start-codex",
    "gemini": "agent-start-gemini",
}
KEYS = ("AGENTSTACK_PROJECT_KEY", "PROJECT_KEY", "AGENTSTACK_PROTECTED_ROOTS")


@unittest.skipIf(os.name == "nt", "POSIX top-level launchers")
class LauncherProjectEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="orrery-top-project-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.install = self.home / "install"
        self.bin = self.install / "bin"
        (self.bin / "lib").mkdir(parents=True)
        self.workspace = self.root / "workspace 日本語"
        self.workspace.mkdir()
        self.protected = self.root / "shared vault"
        self.protected.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.executable("pbcopy", "import sys\nsys.stdin.read()\n")

        self.snapshot = self.executable("snapshot", r'''
import json, os, pathlib, sys
out = pathlib.Path(os.environ["TEST_OUTPUT"]) / (sys.argv[1] + ".json")
out.write_text(json.dumps({
    "cwd": os.getcwd(),
    "env": {key: os.environ.get(key) for key in json.loads(os.environ["TEST_KEYS"])},
}))
''')
        self.tmux = self.executable("tmux-double", r'''
import os, subprocess, sys
args = sys.argv[1:]
if args[0] == "display-message":
    print("TestAgent")
    raise SystemExit(0)
if args[0] == "has-session":
    raise SystemExit(1)
if args[0] == "rename-session":
    raise SystemExit(0)
if args[0] != "new-session":
    raise SystemExit("unexpected tmux mutation: " + repr(args))
env = dict(os.environ)
env.update({
    "AGENTSTACK_PROJECT_KEY": "server-stale-project",
    "PROJECT_KEY": "server-stale-project",
    "AGENTSTACK_PROTECTED_ROOTS": "/server/stale/root",
})
i = 1
cwd = None
while i < len(args) - 1:
    option, value = args[i:i + 2]
    if option == "-e":
        key, value = value.split("=", 1)
        env[key] = value
    elif option == "-c":
        cwd = value
    elif option != "-s":
        raise SystemExit("unexpected option: " + option)
    i += 2
env["TMUX"] = "fake,1,0"
subprocess.run([env.get("TEST_INNER_SHELL", "/bin/bash"), "-c", args[-1]], cwd=cwd, env=env, check=True, timeout=20)
''')

        self.env = {
            "PATH": str(self.root) + os.pathsep + os.environ.get("PATH", os.defpath),
            "HOME": str(self.home),
            "TMPDIR": str(self.root),
            "LC_ALL": "C",
            "SHELL": "/usr/bin/true",
            "AGENTSTACK_HOME": str(self.install),
            "AGENTSTACK_LABEL_PREFIX": f"org.agentstack.test.top-project.{self.root.name}",
            "TEST_OUTPUT": str(self.output),
            "TEST_KEYS": json.dumps(KEYS),
            "TEST_TMUX": str(self.tmux),
            "TEST_SNAPSHOT": str(self.snapshot),
        }
        for name in LAUNCHERS.values():
            shutil.copy2(ROOT / "bin" / name, self.bin / name)
        (self.bin / "lib" / "agentstack-launch.sh").write_text(
            ". " + shlex.quote(str(ROOT / "bin/lib/agentstack-launch.sh")) + "\n"
            'ags_resolve_tmux() { printf "%s\\n" "$TEST_TMUX"; }\n',
            encoding="utf-8",
        )
        (self.bin / "lib" / "agentstack-register.sh").write_text(
            r'''
ags_pick_adjective_scientist_name() { printf 'TestAgent\n'; }
ags_mail_load_token() { :; }
ags_mcp_call() { :; }
ags_start_mail_watcher() { :; }
ags_record_managed_agent() { :; }
ags_registration_token_file() { return 1; }
ags_register_session() {
  "$TEST_SNAPSHOT" registration
  AGS_REGISTERED_AGENT_NAME=TestAgent
  AGS_AGENT_NAME_SUBSTITUTED=0
}
''',
            encoding="utf-8",
        )
        for provider in ("codex", "gemini"):
            (self.bin / f"agentstack-{provider}-bootstrap").write_text(
                '"$TEST_SNAPSHOT" bootstrap\nexport AGENT_NAME=TestAgent\n',
                encoding="utf-8",
            )
        for provider in PROVIDERS:
            fake = self.executable(
                provider,
                'import os, subprocess\n'
                'subprocess.run([os.environ["TEST_SNAPSHOT"], "provider"], check=True)\n',
            )
            self.env[f"AGENTSTACK_{provider.upper()}_BIN"] = str(fake)

        (self.install / "env.sh").write_text(
            "export AGENTSTACK_PROJECT_KEY=installed-project\n"
            f"export AGENTSTACK_PROTECTED_ROOTS={shlex.quote(str(self.protected))}\n",
            encoding="utf-8",
        )

    def executable(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run_launcher(
        self,
        provider: str,
        *args: str | Path,
        inside: bool = False,
        extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        for path in self.output.glob("*.json"):
            path.unlink()
        env = {**self.env, **(extra or {})}
        if inside:
            env["TMUX"] = "fake,1,0"
        else:
            env.pop("TMUX", None)
        return subprocess.run(
            ["/bin/bash", str(self.bin / LAUNCHERS[provider]), *map(str, args)],
            cwd=self.workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def read(self, name: str) -> dict:
        return json.loads((self.output / f"{name}.json").read_text())

    def assert_project(self, name: str, key: str, roots: Path | str | None = None) -> None:
        env = self.read(name)["env"]
        self.assertEqual(env["AGENTSTACK_PROJECT_KEY"], key)
        self.assertEqual(env["PROJECT_KEY"], key)
        if roots is not None:
            self.assertEqual(env["AGENTSTACK_PROTECTED_ROOTS"], str(roots))


    def test_live_selectors_reach_every_provider_and_registration_boundary(self) -> None:
        cases = (
            ({"AGENTSTACK_PROJECT_KEY": "selected-project"}, "selected-project"),
            ({"PROJECT_KEY": "alias-project"}, "alias-project"),
            ({"AGENTSTACK_PROJECT_KEY": "selected-project", "PROJECT_KEY": "stale-alias"}, "selected-project"),
        )
        for provider in PROVIDERS:
            for inside in (True, False):
                for extra, expected in cases:
                    with self.subTest(provider=provider, inside=inside, extra=extra):
                        result = self.run_launcher(provider, self.workspace, inside=inside, extra=extra)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        boundary = "registration" if provider == "claude" else "bootstrap"
                        self.assert_project(boundary, expected, self.protected)
                        self.assert_project("provider", expected, self.protected)
                        self.assertEqual(self.read("provider")["cwd"], str(self.workspace))

    def test_installed_default_still_allows_a_different_workspace(self) -> None:
        for provider in PROVIDERS:
            for inside in (True, False):
                with self.subTest(provider=provider, inside=inside):
                    result = self.run_launcher(provider, self.workspace, inside=inside)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assert_project("provider", "installed-project", self.protected)
                    self.assertEqual(self.read("provider")["cwd"], str(self.workspace))

    def test_project_key_is_literal_even_with_spaces_unicode_and_shell_syntax(self) -> None:
        key = "lecture 日本語 'quoted' ; $(touch SHOULD_NOT_EXIST) `echo no`"
        for shell in ("/bin/bash", shutil.which("zsh")):
            if not shell:
                continue
            for provider in PROVIDERS:
                with self.subTest(shell=shell, provider=provider):
                    result = self.run_launcher(provider, self.workspace, extra={
                        "AGENTSTACK_PROJECT_KEY": key, "TEST_INNER_SHELL": shell,
                    })
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assert_project("provider", key, self.protected)
                    self.assertFalse((self.workspace / "SHOULD_NOT_EXIST").exists())

    def test_helper_precedence_under_bash_and_zsh(self) -> None:
        cases = (
            ({}, "installed-project"),
            ({"AGENTSTACK_PROJECT_KEY": "", "PROJECT_KEY": ""}, "installed-project"),
            ({"AGENTSTACK_PROJECT_KEY": "explicit-project"}, "explicit-project"),
            ({"PROJECT_KEY": "alias-project"}, "alias-project"),
            ({"AGENTSTACK_PROJECT_KEY": "", "PROJECT_KEY": "alias-project"}, "alias-project"),
            ({"AGENTSTACK_PROJECT_KEY": "explicit-project", "PROJECT_KEY": "stale-alias"}, "explicit-project"),
        )
        helper = shlex.quote(str(ROOT / "bin/lib/agentstack-launch.sh"))
        script = f'. {helper}; ags_load_env; ags_load_env; "$TEST_SNAPSHOT" helper'
        for shell in ("/bin/bash", shutil.which("zsh")):
            if not shell:
                continue
            for extra, expected in cases:
                with self.subTest(shell=shell, extra=extra):
                    result = subprocess.run([shell, "-eu", "-c", script], env={**self.env, **extra},
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assert_project("helper", expected, self.protected)

    def test_missing_install_env_does_not_invent_a_project(self) -> None:
        (self.install / "env.sh").unlink()
        helper = shlex.quote(str(ROOT / "bin/lib/agentstack-launch.sh"))
        script = f'. {helper}; ags_load_env; "$TEST_SNAPSHOT" helper'
        for extra, expected in (({}, ""), ({"PROJECT_KEY": "alias-project"}, "alias-project")):
            for shell in ("/bin/bash", shutil.which("zsh")):
                if not shell:
                    continue
                with self.subTest(shell=shell, extra=extra):
                    result = subprocess.run([shell, "-eu", "-c", script], env={**self.env, **extra},
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assert_project("helper", expected)

    def test_no_selected_project_does_not_inherit_stale_tmux_project(self) -> None:
        (self.install / "env.sh").write_text(
            f"export AGENTSTACK_PROTECTED_ROOTS={shlex.quote(str(self.protected))}\n"
        )
        for provider in PROVIDERS:
            for inside in (True, False):
                with self.subTest(provider=provider, inside=inside):
                    result = self.run_launcher(provider, self.workspace, inside=inside)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assert_project("provider", "", self.protected)

    def test_gemini_dry_run_does_not_start_any_provider(self) -> None:
        result = self.run_launcher("gemini", "--dry-run", self.workspace,
                                   extra={"AGENTSTACK_PROJECT_KEY": "explicit-project"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run cwd=", result.stdout)
        self.assertEqual(list(self.output.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
