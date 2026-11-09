"""spawn_gemini_child.sh keeps the caller's explicit project key over env.sh.

A child launched from project A must register in project A, not in the project
the installer saved in env.sh. tmux, agy and the Mail helpers are stubs; the
preregister stub records what it receives and fails, which stops the launcher
right after the registration step.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

from service_teardown import TEST_LABEL_PREFIX

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHILD = ROOT / "hooks" / "spawn_gemini_child.sh"
ENV_SH = "export AGENTSTACK_PROJECT_KEY=/saved/default-project\n"


def launch(tmp_path: pathlib.Path, **caller_env: str) -> tuple[list[str], str]:
    home, stub, repo = tmp_path / "home", tmp_path / "stub", tmp_path / "repo"
    (home / "bin").mkdir(parents=True)
    stub.mkdir()
    repo.mkdir()
    (home / "env.sh").write_text(ENV_SH)
    log = tmp_path / "preregister.log"
    script = ('#!/bin/bash\n'
              f'echo "$1 $2|$AGENTSTACK_PROJECT_KEY" > {log}\nexit 1\n')
    (home / "bin" / "agentstack-preregister-child").write_text(script)
    for name in ("agentstack-gemini-child-mail", "agentstack-gemini-stream", "agentstack-gemini-mcp"):
        (home / "bin" / name).write_text("#!/bin/bash\nexit 0\n")
    (stub / "agy").write_text("#!/bin/bash\nexit 0\n")
    for path in [*(home / "bin").iterdir(), stub / "agy"]:
        path.chmod(0o755)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-q",
                    "--allow-empty", "-m", "init"], check=True)
    env = {"HOME": str(tmp_path), "PATH": f"{stub}:/usr/bin:/bin:/opt/homebrew/bin",
           "AGENTSTACK_HOME": str(home), "AGENTSTACK_LABEL_PREFIX": TEST_LABEL_PREFIX,
           "PARENT_AGENT": "Parent",
           "AGENTSTACK_MCP_URL": "http://127.0.0.1:1/mcp", **caller_env}
    subprocess.run(["bash", str(CHILD), "--unsafe-no-resources", "task", str(repo)],
                   env=env, capture_output=True, text=True, timeout=60)
    flag, _, seen = log.read_text().strip().partition("|")
    return flag.split(), seen


def test_explicit_agentstack_project_key_reaches_preregister(tmp_path):
    args, seen = launch(tmp_path, AGENTSTACK_PROJECT_KEY="/caller/project")
    assert args == ["--project-key", "/caller/project"]
    assert seen == "/caller/project"


def test_explicit_project_key_alias_reaches_preregister(tmp_path):
    args, seen = launch(tmp_path, PROJECT_KEY="/caller/alias")
    assert args == ["--project-key", "/caller/alias"]
    assert seen == "/caller/alias"


def test_without_explicit_key_env_sh_default_is_used(tmp_path):
    args, seen = launch(tmp_path)
    assert args == ["--project-key", "/saved/default-project"]
    assert seen == "/saved/default-project"


def test_explicit_key_does_not_rewrite_env_sh(tmp_path):
    launch(tmp_path, AGENTSTACK_PROJECT_KEY="/caller/project")
    assert (tmp_path / "home" / "env.sh").read_text() == ENV_SH
