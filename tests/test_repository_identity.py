#!/usr/bin/env python3
"""Repository identity primitives; no Mail, tmux, installer, or user HOME.

Both pytest and direct execution use unittest, so neither fixture injection
nor the position of a custom globals() runner changes which tests execute.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hooks" / "project-context.sh"
BASH = "/bin/bash"


@unittest.skipIf(os.name == "nt", "POSIX shell helpers; Windows uses its portable lane")
class RepositoryIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="orrery-identity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.home),
            "TMPDIR": str(self.root),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "AGENTSTACK_HOME": str(self.home / ".agentstack"),
            "AGENTSTACK_PYTHON": sys.executable,
        }
        self.repo = self.root / "repo A"
        self.other = self.root / "repo B"
        self.make_repo(self.repo)
        self.make_repo(self.other)
        self.env_file = self.root / "installed-env.sh"
        self.env_file.write_text("", encoding="utf-8")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + str(self.root / "no-hooks"),
             "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
            cwd=cwd or self.root, env=self.env, capture_output=True,
            text=True, check=True, timeout=20,
        )
        return result.stdout.strip()

    def make_repo(self, path: Path, git_dir: Path | None = None) -> None:
        args = ["-c", "init.defaultBranch=main", "init", "-q"]
        if git_dir is not None:
            args.append("--separate-git-dir=" + str(git_dir))
        self.git(*args, str(path))
        self.git("-c", "user.name=Isolation Test", "-c",
                 "user.email=isolation-test@example.invalid", "-c",
                 "commit.gpgsign=false", "commit", "--allow-empty", "-qm",
                 "isolated fixture", cwd=path)

    def linked_worktree(self, repo: Path | None = None) -> Path:
        linked = self.root / "linked tree"
        self.git("worktree", "add", "-q", "--detach", str(linked),
                 cwd=repo or self.repo)
        return linked

    def call(self, function: str, *args: str | Path,
             env: dict[str, str] | None = None, cwd: Path | None = None,
             check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH, "-euo", "pipefail", "-c",
             'source "$1"; shift; "$@"', "identity-test", str(SCRIPT),
             function, *(str(arg) for arg in args)],
            cwd=cwd or self.root, env={**self.env, **(env or {})},
            capture_output=True, text=True, check=check, timeout=20,
        )

    def test_main_checkout_identity(self) -> None:
        self.assertEqual(self.call("agentstack_repository_key", self.repo).stdout,
                         str(self.repo) + "\n")

    def test_nested_directory_identity(self) -> None:
        nested = self.repo / "src" / "nested"
        nested.mkdir(parents=True)
        self.assertEqual(self.call("agentstack_repository_key", nested).stdout,
                         str(self.repo) + "\n")

    def test_linked_worktrees_share_repository_identity(self) -> None:
        linked = self.linked_worktree()
        self.assertEqual(self.call("agentstack_repository_key", linked).stdout,
                         str(self.repo) + "\n")
        self.assertEqual(self.call("agentstack_same_repository", linked,
                                   self.repo).returncode, 0)

    def test_linked_worktree_retains_own_workspace_root(self) -> None:
        linked = self.linked_worktree()
        nested = linked / "src"
        nested.mkdir()
        self.assertEqual(self.call("agentstack_git_worktree_root", nested).stdout,
                         str(linked) + "\n")

    def test_distinct_repositories_do_not_match(self) -> None:
        result = self.call("agentstack_same_repository", self.repo,
                           self.other, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_same_remote_does_not_merge_independent_clones(self) -> None:
        self.git("remote", "add", "origin", "https://example.invalid/shared.git",
                 cwd=self.repo)
        self.git("remote", "add", "origin", "https://example.invalid/shared.git",
                 cwd=self.other)
        self.assertEqual(self.call("agentstack_same_repository", self.repo,
                                   self.other, check=False).returncode, 1)

    def test_symlink_alias_has_physical_identity(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        self.assertEqual(self.call("agentstack_repository_key", alias).stdout,
                         str(self.repo) + "\n")

    def test_relative_directory_ignores_cdpath_output(self) -> None:
        self.assertEqual(self.call("agentstack_repository_key", self.repo.name,
                                   env={"CDPATH": str(self.root)}).stdout,
                         str(self.repo) + "\n")
        self.assertEqual(self.call("agentstack_physical_dir", self.repo.name,
                                   env={"CDPATH": str(self.root)}).stdout,
                         str(self.repo) + "\n")

    def test_nested_repository_is_not_its_parent(self) -> None:
        nested = self.repo / "nested-repo"
        self.make_repo(nested)
        self.assertEqual(self.call("agentstack_repository_key", nested).stdout,
                         str(nested) + "\n")
        self.assertEqual(self.call("agentstack_same_repository", nested,
                                   self.repo, check=False).returncode, 1)

    def test_separate_git_directory_and_linked_worktree(self) -> None:
        work = self.root / "separate work"
        common = self.root / "git metadata"
        self.make_repo(work, common)
        linked = self.linked_worktree(work)
        for target in (work, linked):
            with self.subTest(target=target.name):
                self.assertEqual(self.call("agentstack_repository_key", target).stdout,
                                 str(common) + "\n")

    def test_bare_repository_has_identity_but_no_worktree_root(self) -> None:
        bare = self.root / "bare.git"
        self.git("init", "--bare", "-q", str(bare))
        self.assertEqual(self.call("agentstack_repository_key", bare).stdout,
                         str(bare) + "\n")
        self.assertEqual(self.call("agentstack_git_worktree_root", bare,
                                   check=False).returncode, 1)

    def test_submodule_is_not_parent_repository(self) -> None:
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
                 str(self.other), "child", cwd=self.repo)
        submodule = self.repo / "child"
        self.assertEqual(self.call("agentstack_repository_key", submodule).stdout,
                         str(self.repo / ".git" / "modules" / "child") + "\n")
        self.assertEqual(self.call("agentstack_same_repository", submodule,
                                   self.repo, check=False).returncode, 1)

    def test_stale_git_selectors_do_not_redirect_probes(self) -> None:
        stale = {
            "GIT_DIR": str(self.other / ".git"),
            "GIT_WORK_TREE": str(self.other),
            "GIT_COMMON_DIR": str(self.other / ".git"),
            "AGENTSTACK_PROJECT_KEY": str(self.other),
            "PROJECT_KEY": str(self.other),
        }
        for function in ("agentstack_repository_key", "agentstack_git_worktree_root"):
            with self.subTest(function=function):
                self.assertEqual(self.call(function, self.repo, env=stale).stdout,
                                 str(self.repo) + "\n")

    def test_stale_git_selectors_do_not_turn_non_git_into_repository(self) -> None:
        directory = self.root / "non-git"
        directory.mkdir()
        stale = {"GIT_DIR": str(self.repo / ".git"),
                 "GIT_WORK_TREE": str(self.repo)}
        result = self.call("agentstack_repository_key", directory, env=stale,
                           check=False)
        self.assertEqual((result.returncode, result.stdout), (1, ""))

    def test_invalid_targets_have_no_repository_identity(self) -> None:
        regular_file = self.root / "file"
        regular_file.touch()
        for target in ("", self.root / "missing", regular_file, self.home):
            with self.subTest(target=str(target)):
                result = self.call("agentstack_repository_key", target, check=False)
                self.assertEqual((result.returncode, result.stdout), (1, ""))

    def test_non_git_directories_do_not_match_as_empty_keys(self) -> None:
        self.assertEqual(self.call("agentstack_same_repository", self.home,
                                   self.home, check=False).returncode, 1)

    def test_normalize_logical_key_without_path_or_shell_expansion(self) -> None:
        logical = "logical:project-$(touch SHOULD_NOT_EXIST)"
        self.assertEqual(self.call("agentstack_normalize_project_key", logical).stdout,
                         logical + "\n")
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_normalize_existing_relative_key(self) -> None:
        self.assertEqual(self.call("agentstack_normalize_project_key", self.repo.name).stdout,
                         str(self.repo) + "\n")

    def test_normalize_explicit_worktree_key_does_not_canonicalize_repository(self) -> None:
        linked = self.linked_worktree()
        self.assertEqual(self.call("agentstack_normalize_project_key", linked).stdout,
                         str(linked) + "\n")

    def test_normalize_nonexistent_absolute_key_resolves_parent_alias(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        self.assertEqual(self.call("agentstack_normalize_project_key", alias / "new").stdout,
                         str(self.repo / "new") + "\n")

    def test_normalize_empty_key(self) -> None:
        self.assertEqual(self.call("agentstack_normalize_project_key", "").stdout, "\n")

    def test_existing_path_does_not_need_python(self) -> None:
        self.assertEqual(self.call("agentstack_normalize_project_key", self.repo,
                                   env={"AGENTSTACK_PYTHON": "/missing/python"}).stdout,
                         str(self.repo) + "\n")

    def test_missing_python_does_not_turn_missing_path_into_success(self) -> None:
        result = self.call("agentstack_normalize_project_key", self.root / "new",
                           env={"AGENTSTACK_PYTHON": "/missing/python"}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_probes_preserve_callers_directory_and_git_selectors(self) -> None:
        code = ('source "$1"; before=$PWD; '
                'agentstack_repository_key "$2" >/dev/null; '
                'agentstack_git_worktree_root "$2" >/dev/null; '
                '[ "$PWD" = "$before" ]; [ "$GIT_DIR" = "$3/.git" ]; '
                '[ "$GIT_WORK_TREE" = "$3" ]; [ "$GIT_COMMON_DIR" = "$3/.git" ]')
        result = subprocess.run(
            [BASH, "-euo", "pipefail", "-c", code, "identity-test", str(SCRIPT),
             str(self.repo), str(self.other)], cwd=self.root,
            env={**self.env, "GIT_DIR": str(self.other / ".git"),
                 "GIT_WORK_TREE": str(self.other),
                 "GIT_COMMON_DIR": str(self.other / ".git")},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_legacy_resolver_precedence_remains_unchanged_in_this_slice(self) -> None:
        self.env_file.write_text("export AGENTSTACK_PROJECT_KEY=installed\n",
                                 encoding="utf-8")
        cases = [({"AGENTSTACK_PROJECT_KEY": "ambient", "PROJECT_KEY": "project"}, "ambient"),
                 ({"PROJECT_KEY": "project"}, "project"), ({}, "installed")]
        for extra_env, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.call("agentstack_resolve_project_key", self.repo,
                                           self.env_file, env=extra_env).stdout,
                                 expected + "\n")

    def test_legacy_resolver_cwd_and_explicit_fallback_are_unchanged(self) -> None:
        self.assertEqual(self.call("agentstack_resolve_project_key", "",
                                   self.env_file, "1").stdout, str(self.root) + "\n")
        self.assertEqual(self.call("agentstack_resolve_project_key", "",
                                   self.env_file, "0").stdout, "\n")
        self.assertEqual(self.call("agentstack_resolve_project_key", "fallback",
                                   self.env_file, "0").stdout, "fallback\n")

    def test_legacy_protected_roots_are_unchanged(self) -> None:
        self.env_file.write_text("export AGENTSTACK_PROTECTED_ROOTS=installed-roots\n",
                                 encoding="utf-8")
        self.assertEqual(self.call("agentstack_resolve_protected_roots", "project", "",
                                   self.env_file).stdout, "installed-roots\n")
        self.assertEqual(self.call("agentstack_resolve_protected_roots", "project", "live",
                                   self.env_file).stdout, "project\n")
        self.assertEqual(self.call("agentstack_resolve_protected_roots", "project", "live",
                                   self.env_file, env={"AGENTSTACK_PROTECTED_ROOTS": "ambient-roots"}).stdout,
                         "ambient-roots\n")

    def test_legacy_env_parser_never_sources_file(self) -> None:
        marker = self.root / "NOT_EXECUTED"
        self.env_file.write_text(f'touch "{marker}"\nexport AGENTSTACK_PROJECT_KEY=literal\n',
                                 encoding="utf-8")
        self.assertEqual(self.call("agentstack_installed_env_value", "AGENTSTACK_PROJECT_KEY",
                                   self.env_file).stdout, "literal")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
