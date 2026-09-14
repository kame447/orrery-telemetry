#!/usr/bin/env python3
"""Read-only workspace context: real isolated Git fixtures, no Mail or tmux."""
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


SCRIPT = Path(__file__).resolve().parents[1] / "hooks" / "project-context.sh"
BASH = "/bin/bash"
FUNCTION = "agentstack_resolve_invocation_context"


@unittest.skipIf(os.name == "nt", "POSIX workspace context")
class InvocationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="orrery-context-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.home), "TMPDIR": str(self.root), "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "AGENTSTACK_PYTHON": sys.executable,
            "AGENTSTACK_HOME": str(self.home / ".agentstack"),
        }
        self.repo = self.root / "repo A"
        self.other = self.root / "repo B"
        self.make_repo(self.repo)
        self.make_repo(self.other)
        self.plain = self.root / "plain workspace"
        self.plain.mkdir()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + str(self.root / "no-hooks"),
             "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
            cwd=cwd or self.root, env=self.env, capture_output=True,
            text=True, check=True, timeout=20,
        )
        return result.stdout.strip()

    def make_repo(self, path: Path, metadata: Path | None = None) -> None:
        args = ["-c", "init.defaultBranch=main", "init", "-q"]
        if metadata is not None:
            args.append("--separate-git-dir=" + str(metadata))
        self.git(*args, str(path))
        self.git("-c", "user.name=Context Test", "-c",
                 "user.email=context@example.invalid", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-qm", "fixture", cwd=path)

    def linked(self, repo: Path | None = None) -> Path:
        path = self.root / "linked tree"
        self.git("worktree", "add", "-q", "--detach", str(path), cwd=repo or self.repo)
        return path

    def call(self, *args: str | Path, env: dict[str, str] | None = None,
             cli: bool = False, prelude: str = "") -> subprocess.CompletedProcess[str]:
        command = ([BASH, str(SCRIPT), "resolve-invocation-context"] if cli else
                   [BASH, "-euo", "pipefail", "-c",
                    'source "$1"; shift; ' + prelude + ' "$@"', "context-test",
                    str(SCRIPT), FUNCTION])
        return subprocess.run(
            [*command, *(str(arg) for arg in args)], cwd=self.root,
            env={**self.env, **(env or {})}, capture_output=True, text=True, timeout=20,
        )

    def context(self, *args: str | Path, **kwargs: object) -> dict:
        result = self.call(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def assert_rejected(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def shim(self, body: str) -> dict[str, str]:
        directory = self.root / "shim"
        directory.mkdir()
        executable = directory / "git"
        real_git = shutil.which("git", path=self.env["PATH"])
        self.assertIsNotNone(real_git)
        executable.write_text("#!/bin/sh\n" + body + "\nexec " +
                              shlex.quote(real_git) + ' "$@"\n', encoding="utf-8")
        executable.chmod(0o755)
        return {"PATH": str(directory) + os.pathsep + self.env["PATH"]}

    def test_main_and_nested_target_keep_full_workspace_protected(self) -> None:
        nested = self.repo / "src"
        nested.mkdir()
        for target in (self.repo, nested):
            with self.subTest(target=target):
                self.assertEqual(self.context(target), {
                    "project_key": str(self.repo), "repository_key": str(self.repo),
                    "work_dir": str(target), "worktree_root": str(self.repo),
                    "protected_roots": [str(self.repo)],
                })

    def test_linked_identity_shared_but_working_roots_stay_local(self) -> None:
        linked = self.linked()
        context = self.context(linked)
        self.assertEqual(context["project_key"], str(self.repo))
        self.assertEqual(context["repository_key"], str(self.repo))
        self.assertEqual(context["work_dir"], str(linked))
        self.assertEqual(context["worktree_root"], str(linked))
        self.assertEqual(context["protected_roots"], [str(linked)])

    def test_real_clone_and_other_repository_remain_distinct(self) -> None:
        clone = self.root / "clone"
        self.git("clone", "-q", str(self.repo), str(clone))
        contexts = [self.context(path) for path in (self.repo, self.other, clone)]
        self.assertEqual(len({context["repository_key"] for context in contexts}), 3)
        self.assertEqual(len({context["project_key"] for context in contexts}), 3)

    def test_alias_and_relative_target_are_physical(self) -> None:
        linked = self.linked()
        alias = self.root / "alias"
        alias.symlink_to(linked, target_is_directory=True)
        self.assertEqual(self.context(alias.name, env={"CDPATH": str(self.root)}),
                         self.context(linked))

    def test_explicit_key_is_a_namespace_not_a_protected_path(self) -> None:
        for key in ("logical:key", str(self.other), 'name-$(touch NOT_EXECUTED)-"quoted"'):
            with self.subTest(key=key):
                context = self.context(self.repo, key, "unused-fallback")
                self.assertEqual(context["project_key"], key)
                self.assertEqual(context["repository_key"], str(self.repo))
                self.assertEqual(context["protected_roots"], [str(self.repo)])
        self.assertFalse((self.root / "NOT_EXECUTED").exists())

    def test_explicit_linked_key_does_not_change_repository_binding(self) -> None:
        linked = self.linked()
        context = self.context(linked, linked)
        self.assertEqual(context["project_key"], str(linked))
        self.assertEqual(context["repository_key"], str(self.repo))
        self.assertEqual(context["protected_roots"], [str(linked)])

    def test_stale_environment_and_context_marker_are_not_authority(self) -> None:
        stale = {"AGENTSTACK_PROJECT_KEY": str(self.other), "PROJECT_KEY": str(self.other),
                 "AGENTSTACK_PROTECTED_ROOTS": str(self.other),
                 "AGENTSTACK_PROJECT_CONTEXT": "1",
                 "AGENTSTACK_PROJECT_REPOSITORY": str(self.other),
                 "GIT_DIR": str(self.other / ".git"), "GIT_WORK_TREE": str(self.other),
                 "GIT_COMMON_DIR": str(self.other / ".git")}
        linked = self.linked()
        for target in (linked, self.plain):
            with self.subTest(target=target):
                self.assertEqual(self.context(target, env=stale), self.context(target))

    def test_installed_key_and_roots_are_neither_read_nor_sourced(self) -> None:
        install = self.home / ".agentstack"
        install.mkdir()
        marker = self.root / "ENV_EXECUTED"
        (install / "env.sh").write_text(
            f'touch "{marker}"\nexport AGENTSTACK_PROJECT_KEY=foreign\n'
            f'export AGENTSTACK_PROTECTED_ROOTS="{self.other}"\n', encoding="utf-8")
        context = self.context(self.plain)
        self.assertEqual(context["project_key"], str(self.plain))
        self.assertEqual(context["protected_roots"], [str(self.plain)])
        self.assertFalse(marker.exists())

    def test_non_git_fallback_changes_only_namespace(self) -> None:
        for key, fallback, expected in (("", "", str(self.plain)),
                                        ("", str(self.other), str(self.other)),
                                        ("explicit", "fallback", "explicit")):
            with self.subTest(key=key, fallback=fallback):
                self.assertEqual(self.context(self.plain, key, fallback), {
                    "project_key": expected, "repository_key": None,
                    "work_dir": str(self.plain), "worktree_root": None,
                    "protected_roots": [str(self.plain)],
                })

    def test_separate_metadata_does_not_become_protected_workspace(self) -> None:
        work = self.root / "separate"
        metadata = self.root / "metadata"
        self.make_repo(work, metadata)
        for target in (work, self.linked(work)):
            with self.subTest(target=target):
                context = self.context(target)
                self.assertEqual(context["project_key"], str(metadata))
                self.assertEqual(context["repository_key"], str(metadata))
                self.assertEqual(context["protected_roots"], [str(target)])

    def test_submodule_and_nested_repository_are_not_parent_workspace(self) -> None:
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(self.other), "submodule", cwd=self.repo)
        nested = self.repo / "nested"
        self.make_repo(nested)
        for target in (self.repo / "submodule", nested):
            with self.subTest(target=target):
                context = self.context(target)
                self.assertNotEqual(context["repository_key"], str(self.repo))
                self.assertEqual(context["protected_roots"], [str(target)])

    def test_bare_or_metadata_target_cannot_claim_a_working_context(self) -> None:
        bare = self.root / "bare.git"
        self.git("init", "-q", "--bare", str(bare))
        for target in (bare, self.repo / ".git"):
            with self.subTest(target=target):
                self.assert_rejected(self.call(target, "explicit", "fallback"))

    def test_invalid_target_is_not_rescued_by_explicit_key(self) -> None:
        regular = self.root / "file"
        regular.touch()
        for target in ("", self.root / "missing", regular):
            with self.subTest(target=target):
                self.assert_rejected(self.call(target, "explicit", "fallback"))

    def test_broken_metadata_rejected_even_with_explicit_key(self) -> None:
        (self.plain / ".git").write_text("gitdir: /missing/context-metadata\n")
        nested = self.plain / "nested"
        nested.mkdir()
        for key in ("", "explicit"):
            with self.subTest(key=key):
                self.assert_rejected(self.call(nested, key, "fallback"))

    def test_dangling_metadata_is_not_non_git(self) -> None:
        (self.plain / ".git").symlink_to(self.root / "missing")
        self.assert_rejected(self.call(self.plain, "explicit", "fallback"))

    def test_git_execution_failure_is_not_non_git(self) -> None:
        env = self.shim("echo 'fatal: synthetic IO failure' >&2\nexit 23")
        for key in ("", "explicit"):
            with self.subTest(key=key):
                self.assert_rejected(self.call(self.plain, key, "fallback", env=env))

    def test_missing_git_is_not_bypassed_by_explicit_namespace(self) -> None:
        empty = self.root / "empty-bin"
        empty.mkdir()
        self.assert_rejected(self.call(self.repo, "explicit", env={"PATH": str(empty)}))

    def test_worktree_probe_failure_does_not_return_partial_context(self) -> None:
        env = self.shim('case "$*" in *--show-toplevel*) exit 23;; esac')
        self.assert_rejected(self.call(self.repo, "explicit", env=env))

    def test_repository_worktree_mismatch_is_rejected(self) -> None:
        env = self.shim('case "$*" in *--show-toplevel*) printf "%s\\n" ' +
                        shlex.quote(str(self.other)) + '; exit 0;; esac')
        self.assert_rejected(self.call(self.repo, env=env))

    def test_target_outside_claimed_worktree_is_rejected(self) -> None:
        prelude = ('agentstack_git_worktree_root() { printf "%s\\n" ' +
                   shlex.quote(str(self.other)) + '; }; '
                   'agentstack_repository_key() { printf "%s\\n" ' +
                   shlex.quote(str(self.repo)) + '; }; ')
        self.assert_rejected(self.call(self.repo, prelude=prelude))

    def test_missing_python_does_not_return_partial_context(self) -> None:
        self.assert_rejected(self.call(self.repo, env={"AGENTSTACK_PYTHON": "/missing/python"}))

    def test_cli_and_library_match_and_reject_bad_arity(self) -> None:
        self.assertEqual(self.context(self.repo, "chosen", cli=True),
                         self.context(self.repo, "chosen"))
        for cli in (False, True):
            for args in ((), (self.repo, "key", "fallback", "excess")):
                with self.subTest(cli=cli, args=args):
                    result = self.call(*args, cli=cli)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")

    def test_success_and_failure_do_not_change_caller_environment_or_cwd(self) -> None:
        for target in (self.repo, self.root / "missing"):
            with self.subTest(target=target):
                code = ('source "$1"; before="$(export -p)"; before_pwd="$PWD"; '
                        'agentstack_resolve_invocation_context "$2" >/dev/null 2>&1 || :; '
                        '[ "$(export -p)" = "$before" ]; [ "$PWD" = "$before_pwd" ]')
                result = subprocess.run(
                    [BASH, "-euo", "pipefail", "-c", code, "context-test", str(SCRIPT), str(target)],
                    cwd=self.root, env={**self.env, "AGENTSTACK_PROJECT_KEY": "foreign",
                                       "AGENTSTACK_PROTECTED_ROOTS": "foreign-roots",
                                       "AGENTSTACK_PROJECT_CONTEXT": "1"},
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
