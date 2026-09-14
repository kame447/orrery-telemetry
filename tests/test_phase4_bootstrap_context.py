"""Phase4 bootstrap context boundary for the Codex and Gemini bootstraps.

The real bootstrap, registration library and project-context resolver run
against real Git fixtures. Only external effects are replaced: ``curl`` (ORRERY
Mail transport), ``tmux`` and ``pbcopy`` are PATH doubles, HOME/AGENTSTACK_HOME/
runtime live in a temporary directory, and the installed ``env.sh`` carries a
stale foreign project. No agent, provider or Mail service is started, and the
Mail double records whether a token was sent without ever storing its value.
"""
from __future__ import annotations

import hashlib
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
PROVIDERS = ("codex", "gemini")
CONTEXT_KEYS = (
    "AGENTSTACK_PROJECT_KEY", "PROJECT_KEY", "AGENTSTACK_PROJECT_REPOSITORY",
    "AGENTSTACK_PROJECT_WORK_DIR", "AGENTSTACK_PROJECT_WORKTREE_ROOT",
    "AGENTSTACK_PROTECTED_ROOTS", "AGENTSTACK_PROJECT_CONTEXT_JSON",
)
STALE_TOKEN = "stale-owner-token-must-not-be-sent"


@unittest.skipIf(os.name == "nt", "POSIX bootstraps")
class Phase4BootstrapContextTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="orrery-p4-bootstrap-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / "home"
        self.install = self.home / "install"
        self.runtime = self.home / "runtime"
        self.output = self.root / "output"
        self.fake_bin = self.root / "fake-bin"
        for path in (self.install / "bin" / "lib", self.install / "hooks",
                     self.runtime, self.output, self.fake_bin):
            path.mkdir(parents=True)

        # Real product code, installed-layout copies (SCRIPT_DIR/../hooks etc.).
        for provider in PROVIDERS:
            name = f"agentstack-{provider}-bootstrap"
            shutil.copy2(ROOT / "bin" / name, self.install / "bin" / name)
        for lib in (ROOT / "bin" / "lib").glob("*.sh"):
            shutil.copy2(lib, self.install / "bin" / "lib" / lib.name)
        shutil.copy2(ROOT / "hooks" / "project-context.sh",
                     self.install / "hooks" / "project-context.sh")

        self.env = {
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
            "HOME": str(self.home), "TMPDIR": str(self.root), "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "AGENTSTACK_PYTHON": sys.executable,
            "AGENTSTACK_HOME": str(self.install),
            "AGENTSTACK_RUNTIME_DIR": str(self.runtime),
            "AGENTSTACK_LABEL_PREFIX": f"org.agentstack.p4-bootstrap.{self.root.name}",
            "AGENTSTACK_MCP_URL": "http://mail-double.invalid/mcp",
            "AGENTSTACK_MAIL_HTTP_BEARER_MODE": "disabled",
            "AGENTSTACK_CONTACT_POLICY": "skip",
            "AGENTSTACK_SCIENTISTS_JSON": str(ROOT / "dashboard" / "scientist_portraits.json"),
            "TEST_OUTPUT": str(self.output), "TEST_KEYS": json.dumps(CONTEXT_KEYS),
            "TEST_STALE_TOKEN": STALE_TOKEN,
        }

        self.repo = self.root / "repo A"
        self.other = self.root / "repo B"
        for path in (self.repo, self.other):
            self.git("-c", "init.defaultBranch=main", "init", "-q", str(path))
            self.commit(path)
        (self.repo / "pkg" / "sub").mkdir(parents=True)
        self.linked = self.root / "linked A"
        self.git("worktree", "add", "-q", "--detach", str(self.linked), cwd=self.repo)
        (self.linked / "deep").mkdir()
        self.clone = self.root / "clone of A"
        self.git("clone", "-q", str(self.repo), str(self.clone))
        self.plain = self.root / "plain workspace"
        self.plain.mkdir()

        self.write_doubles()
        (self.install / "env.sh").write_text(
            "export AGENTSTACK_PROJECT_KEY=installed-foreign\n"
            "export PROJECT_KEY=installed-foreign\n"
            f"export AGENTSTACK_PROTECTED_ROOTS={shlex.quote(str(self.other))}\n"
            f"export AGENTSTACK_RUNTIME_DIR={shlex.quote(str(self.runtime))}\n"
            "export AGENTSTACK_MCP_URL=http://mail-double.invalid/mcp\n")

        other_context = self.resolve_context(self.other)
        self.stale_foreign = {
            "AGENTSTACK_PROJECT_KEY": str(self.other), "PROJECT_KEY": "ambient-namespace",
            "AGENTSTACK_PROJECT_REPOSITORY": str(self.other),
            "AGENTSTACK_PROJECT_WORK_DIR": str(self.other),
            "AGENTSTACK_PROJECT_WORKTREE_ROOT": str(self.other),
            "AGENTSTACK_PROTECTED_ROOTS": str(self.other),
            "AGENTSTACK_PROJECT_CONTEXT": "1",
            "AGENTSTACK_PROJECT_CONTEXT_JSON": other_context,
            "AGENTSTACK_LOOKUP_PROJECT_KEY": str(self.other),
            "GIT_DIR": str(self.other / ".git"), "GIT_WORK_TREE": str(self.other),
            "GIT_COMMON_DIR": str(self.other / ".git"),
            "AGENT_NAME": "Stale-Curie", "PARENT_AGENT": "Stale-Parent",
            "CHILD_REGISTRATION_TOKEN": STALE_TOKEN,
        }

    # ------------------------------------------------------------------ fixture
    def git(self, *args: str, cwd: Path | None = None) -> str:
        env = {k: v for k, v in self.env.items() if not k.startswith("GIT_DIR")}
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + str(self.root / "no-hooks"),
             "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
            cwd=cwd or self.root, env=env, capture_output=True, text=True,
            check=True, timeout=30)
        return result.stdout

    def commit(self, path: Path) -> None:
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm",
                 "fixture", cwd=path)

    def executable(self, path: Path, source: str) -> None:
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)

    def write_doubles(self) -> None:
        # ORRERY Mail transport double: answers the MCP tools the bootstrap uses
        # and appends a token-free record of every call.
        self.executable(self.fake_bin / "curl", r'''
import json, os, pathlib, sys
if os.environ.get("TEST_MAIL_DOWN") == "1":
    raise SystemExit(7)
payload = json.loads(sys.stdin.read() or "{}")
params = payload.get("params") or {}
tool = params.get("name", "")
args = dict(params.get("arguments") or {})
token = args.pop("registration_token", None)
record = {"tool": tool, "args": args, "token_sent": token is not None,
          "token_is_stale": token == os.environ.get("TEST_STALE_TOKEN")}
with open(pathlib.Path(os.environ["TEST_OUTPUT"]) / "mail-calls.jsonl", "a") as handle:
    handle.write(json.dumps(record) + "\n")
def text(obj, error=False):
    body = {"content": [{"type": "text", "text": json.dumps(obj)}]}
    if error:
        body["isError"] = True
    return {"jsonrpc": "2.0", "id": "1", "result": body}
if tool == "health_check":
    reply = text({"status": "ok"})
elif tool == "whois":
    if token is None:
        reply = {"jsonrpc": "2.0", "id": "1", "result": {"isError": True, "content": [
            {"type": "text", "text": "Agent '%s' not found in project" % args.get("agent_name")}]}}
    else:
        reply = text({"name": args.get("agent_name")})
elif tool == "ensure_project":
    reply = text({"id": 1, "human_key": args.get("human_key")})
elif tool == "register_agent":
    reply = text({"id": 42, "name": args.get("name"),
                  "project_key": args.get("project_key"), "registration_token": token})
else:
    reply = text({"ok": True})
sys.stdout.write(json.dumps(reply))
''')
        self.executable(self.fake_bin / "tmux", r'''
import json, os, pathlib, sys
with open(pathlib.Path(os.environ["TEST_OUTPUT"]) / "tmux-calls.jsonl", "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
raise SystemExit(1)
''')
        self.executable(self.fake_bin / "pbcopy", "import sys; sys.stdin.read()\n")
        self.snapshot = self.root / "snapshot"
        self.executable(self.snapshot, r'''
import json, os, pathlib, sys
out = pathlib.Path(os.environ["TEST_OUTPUT"]) / (sys.argv[1] + ".json")
out.write_text(json.dumps({
    "status": int(sys.argv[2]) if len(sys.argv) > 2 else None,
    "env": {key: os.environ.get(key) for key in json.loads(os.environ["TEST_KEYS"])},
    "agent_name": os.environ.get("AGENT_NAME"),
}))
''')

    def resolve_context(self, target: Path, explicit: str = "") -> str:
        result = subprocess.run(
            ["/bin/bash", str(self.install / "hooks" / "project-context.sh"),
             "resolve-invocation-context", str(target), explicit],
            env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def launcher_transport(self, target: Path, explicit: str = "") -> str:
        """Produce --expected-context with the real launcher helper."""
        script = (
            'AGS_PROG=agent-start-codex; BIN_DIR="$1"; . "$BIN_DIR/lib/agentstack-launch.sh"; '
            'ags_prepare_top_level_context "$2" "$3" || exit 1; '
            'printf "%s" "${AGS_INVOCATION_CONTEXT_JSON:-}"')
        result = subprocess.run(
            ["/bin/bash", "-c", script, "launcher", str(self.install / "bin"),
             str(target), explicit],
            env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout, "launcher produced no invocation transport")
        return result.stdout

    def run_bootstrap(self, provider: str, *args: str | Path,
                      ambient: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        for name in ("mail-calls.jsonl", "tmux-calls.jsonl", "after.json", "provider.json"):
            (self.output / name).unlink(missing_ok=True)
        self.runtime_before = self.runtime_files()
        env = {**self.env, **(ambient if ambient is not None else self.stale_foreign)}
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        bootstrap = self.install / "bin" / f"agentstack-{provider}-bootstrap"
        # Same shape as the launcher's pane command: `source bootstrap && provider`.
        script = ('boot="$1"; snap="$2"; shift 2; '
                  'source "$boot" "$@" && "$snap" provider; rc=$?; "$snap" after "$rc"')
        return subprocess.run(
            ["/bin/bash", "-c", script, "bootstrap-test", str(bootstrap), str(self.snapshot),
             *map(str, args)],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=120)

    def runtime_files(self) -> dict[str, int]:
        return {str(p.relative_to(self.runtime)): p.stat().st_mtime_ns
                for p in self.runtime.rglob("*") if p.is_file()}

    def mail_calls(self) -> list[dict]:
        path = self.output / "mail-calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def after(self) -> dict:
        return json.loads((self.output / "after.json").read_text())

    def provider_ran(self) -> bool:
        return (self.output / "provider.json").exists()

    # --------------------------------------------------------------- assertions
    def assert_registered_context(self, result: subprocess.CompletedProcess[str], *,
                                  key: str, repository: Path | None, work_dir: Path,
                                  worktree: Path | None) -> None:
        detail = result.stderr[-2000:]
        self.assertTrue(self.provider_ran(), detail)
        env = self.after()["env"]
        root = worktree or work_dir
        self.assertEqual(env["AGENTSTACK_PROJECT_KEY"], key, detail)
        self.assertEqual(env["PROJECT_KEY"], key, detail)
        self.assertEqual(env["AGENTSTACK_PROJECT_REPOSITORY"] or None,
                         str(repository) if repository else None)
        self.assertEqual(env["AGENTSTACK_PROJECT_WORK_DIR"], str(work_dir))
        self.assertEqual(env["AGENTSTACK_PROJECT_WORKTREE_ROOT"] or None,
                         str(worktree) if worktree else None)
        self.assertEqual(env["AGENTSTACK_PROTECTED_ROOTS"], str(root))
        exported = json.loads(env["AGENTSTACK_PROJECT_CONTEXT_JSON"])
        self.assertEqual(exported["project_key"], key)
        self.assertEqual(exported["protected_roots"], [str(root)])

        calls = self.mail_calls()
        by_tool: dict[str, list[dict]] = {}
        for call in calls:
            by_tool.setdefault(call["tool"], []).append(call)
        self.assertIn("ensure_project", by_tool, detail)
        self.assertIn("register_agent", by_tool, detail)
        for call in by_tool["ensure_project"]:
            self.assertEqual(call["args"]["human_key"], key)
        for call in calls:
            if "project_key" in call["args"]:
                self.assertEqual(call["args"]["project_key"], key, call["tool"])
            self.assertFalse(call["token_is_stale"], call["tool"])
        register = by_tool["register_agent"][-1]
        self.assertNotEqual(register["args"]["name"], "Stale-Curie")
        self.assertIn(str(work_dir), register["args"]["task_description"])
        self.assertNotIn(STALE_TOKEN, result.stdout + result.stderr)

    def assert_failed_without_side_effects(self, result: subprocess.CompletedProcess[str]) -> None:
        detail = result.stderr[-2000:]
        self.assertFalse(self.provider_ran(), detail)
        self.assertNotEqual(self.after()["status"], 0, detail)
        self.assertEqual(self.mail_calls(), [], detail)
        self.assertFalse((self.output / "tmux-calls.jsonl").exists())
        self.assertEqual(self.runtime_files(), self.runtime_before,
                         "local ownership/token state changed before failure")
        self.assertNotIn(STALE_TOKEN, result.stdout + result.stderr)

    # -------------------------------------------------------------------- tests
    def test_bare_bootstrap_derives_linked_worktree_despite_stale_installed_and_ambient(self) -> None:
        target = self.linked / "deep"
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, target)
                self.assert_registered_context(result, key=str(self.repo), repository=self.repo,
                                               work_dir=target, worktree=self.linked)

    def test_bare_bootstrap_in_main_checkout_protects_main_worktree(self) -> None:
        target = self.repo / "pkg" / "sub"
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, target)
                self.assert_registered_context(result, key=str(self.repo), repository=self.repo,
                                               work_dir=target, worktree=self.repo)

    def test_top_level_argv_namespace_is_literal_and_keeps_actual_roots(self) -> None:
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, "--top-level", "--project-key", "team-x",
                                            self.linked)
                self.assert_registered_context(result, key="team-x", repository=self.repo,
                                               work_dir=self.linked, worktree=self.linked)

    def test_same_repository_stale_custom_namespace_env_is_not_an_override(self) -> None:
        custom_context = self.resolve_context(self.linked, "team-x")
        ambient = {
            "AGENTSTACK_PROJECT_KEY": "team-x", "PROJECT_KEY": "team-x",
            "AGENTSTACK_PROJECT_REPOSITORY": str(self.repo),
            "AGENTSTACK_PROJECT_WORK_DIR": str(self.linked),
            "AGENTSTACK_PROJECT_WORKTREE_ROOT": str(self.linked),
            "AGENTSTACK_PROTECTED_ROOTS": str(self.linked),
            "AGENTSTACK_PROJECT_CONTEXT": "1",
            "AGENTSTACK_PROJECT_CONTEXT_JSON": custom_context,
        }
        for provider in PROVIDERS:
            for mode in ((), ("--top-level",)):
                with self.subTest(provider=provider, mode=mode):
                    result = self.run_bootstrap(provider, *mode, self.linked, ambient=ambient)
                    self.assert_registered_context(result, key=str(self.repo),
                                                   repository=self.repo, work_dir=self.linked,
                                                   worktree=self.linked)

    def test_expected_context_from_launcher_is_accepted_including_reformatted_json(self) -> None:
        for explicit in ("", "team-x"):
            transport = self.launcher_transport(self.linked, explicit)
            pretty = json.dumps(json.loads(transport), indent=2, sort_keys=True)
            for provider in PROVIDERS:
                for label, value in (("launcher", transport), ("reformatted", pretty)):
                    with self.subTest(provider=provider, explicit=explicit, form=label):
                        args = ["--top-level", "--expected-context", value]
                        if explicit:
                            args += ["--project-key", explicit]
                        result = self.run_bootstrap(provider, *args, self.linked)
                        self.assert_registered_context(
                            result, key=explicit or str(self.repo), repository=self.repo,
                            work_dir=self.linked, worktree=self.linked)

    def test_malformed_or_mismatched_expected_context_fails_before_mail_or_provider(self) -> None:
        good = json.loads(self.launcher_transport(self.linked))
        custom = json.loads(self.launcher_transport(self.linked, "team-x"))

        def mutate(**changes: object) -> str:
            value = json.loads(json.dumps(good))
            for dotted, replacement in changes.items():
                target = value
                *parents, leaf = dotted.split("__")
                for part in parents:
                    target = target[part]
                target[leaf] = replacement
            return json.dumps(value)

        cases = {
            "not-json": (["--expected-context", "{not json"], self.linked),
            "schema": (["--expected-context", mutate(schema_version=2)], self.linked),
            "kind": (["--expected-context", mutate(kind="delegated")], self.linked),
            "roots-widened": (["--expected-context",
                               mutate(context__protected_roots=[str(self.repo)])], self.linked),
            "repository-swapped": (["--expected-context",
                                    mutate(context__repository_key=str(self.other))], self.linked),
            "other-target": (["--expected-context", self.launcher_transport(self.other)],
                             self.linked),
            "transport-namespace-without-argv": (["--expected-context", json.dumps(custom)],
                                                 self.linked),
            "argv-namespace-without-transport": (["--project-key", "team-x",
                                                  "--expected-context", json.dumps(good)],
                                                 self.linked),
        }
        for provider in PROVIDERS:
            for label, (extra, target) in cases.items():
                with self.subTest(provider=provider, case=label):
                    result = self.run_bootstrap(provider, "--top-level", *extra, target)
                    self.assert_failed_without_side_effects(result)

    def test_independent_clone_is_not_the_origin_repository(self) -> None:
        origin_ambient = {
            **self.stale_foreign,
            "AGENTSTACK_PROJECT_KEY": str(self.repo), "PROJECT_KEY": str(self.repo),
            "AGENTSTACK_PROJECT_REPOSITORY": str(self.repo),
            "AGENTSTACK_PROJECT_CONTEXT_JSON": self.resolve_context(self.repo),
        }
        for provider in PROVIDERS:
            with self.subTest(provider=provider, case="bare"):
                result = self.run_bootstrap(provider, self.clone, ambient=origin_ambient)
                self.assert_registered_context(result, key=str(self.clone), repository=self.clone,
                                               work_dir=self.clone, worktree=self.clone)
            with self.subTest(provider=provider, case="origin-transport"):
                result = self.run_bootstrap(
                    provider, "--top-level", "--expected-context",
                    self.launcher_transport(self.repo), self.clone, ambient=origin_ambient)
                self.assert_failed_without_side_effects(result)

    def test_non_git_target_ignores_ambient_key_and_honors_explicit_argv(self) -> None:
        for provider in PROVIDERS:
            with self.subTest(provider=provider, case="ambient-only"):
                result = self.run_bootstrap(provider, self.plain)
                self.assert_registered_context(result, key=str(self.plain), repository=None,
                                               work_dir=self.plain, worktree=None)
            with self.subTest(provider=provider, case="ambient-custom-top-level"):
                ambient = {**self.stale_foreign, "AGENTSTACK_PROJECT_KEY": "custom",
                           "PROJECT_KEY": "custom",
                           "AGENTSTACK_PROJECT_WORK_DIR": str(self.plain),
                           "AGENTSTACK_PROTECTED_ROOTS": str(self.plain),
                           "AGENTSTACK_PROJECT_CONTEXT_JSON":
                               self.resolve_context(self.plain, "custom")}
                result = self.run_bootstrap(provider, "--top-level", self.plain, ambient=ambient)
                self.assert_registered_context(result, key=str(self.plain), repository=None,
                                               work_dir=self.plain, worktree=None)
            with self.subTest(provider=provider, case="explicit"):
                result = self.run_bootstrap(provider, "--top-level", "--project-key", "team-x",
                                            self.plain)
                self.assert_registered_context(result, key="team-x", repository=None,
                                               work_dir=self.plain, worktree=None)

    def test_namespace_options_require_top_level_and_fail_before_side_effects(self) -> None:
        transport = self.launcher_transport(self.linked)
        cases = {
            "project-key": ["--project-key", "team-x", str(self.linked)],
            "expected-context": ["--expected-context", transport, str(self.linked)],
            "empty-project-key": ["--top-level", "--project-key", "", str(self.linked)],
            "two-work-dirs": ["--top-level", str(self.linked), str(self.repo)],
            "unknown-option": ["--top-level", "--trust-env", str(self.linked)],
        }
        for provider in PROVIDERS:
            for label, args in cases.items():
                with self.subTest(provider=provider, case=label):
                    result = self.run_bootstrap(provider, *args)
                    self.assert_failed_without_side_effects(result)

    def test_top_level_never_adopts_inherited_reserved_identity_or_token(self) -> None:
        ambient = {**self.stale_foreign, "AGENTSTACK_RESERVED_IDENTITY": "1"}
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, "--top-level", self.linked, ambient=ambient)
                self.assert_registered_context(result, key=str(self.repo), repository=self.repo,
                                               work_dir=self.linked, worktree=self.linked)
                register = [c for c in self.mail_calls() if c["tool"] == "register_agent"][-1]
                self.assertTrue(register["token_sent"])
                self.assertFalse(register["token_is_stale"])
                self.assertNotEqual(self.after()["agent_name"], "Stale-Curie")



class Phase4BootstrapLocalOwnershipFallbackTests(Phase4BootstrapContextTests):
    """A registration refused for local ownership is not a Mail outage (#801).

    The bootstrap preselects an Adjective-Scientist name before any network
    call so that a real outage can still launch. When Mail answers but the
    registration library refuses that name because it is already owned locally
    by another project, starting the provider under the same name would run two
    agents as one identity.
    """

    def setUp(self) -> None:
        super().setUp()
        roster = self.root / "one-scientist.json"
        roster.write_text(json.dumps({"Curie": {}}))
        self.env["AGENTSTACK_SCIENTISTS_JSON"] = str(roster)
        adjectives = subprocess.run(
            ["/bin/bash", "-c", '. "$1"; ags_adjective_list',
             "adjectives", str(self.install / "bin" / "lib" / "agentstack-scientists.sh")],
            env=self.env, capture_output=True, text=True, check=True, timeout=30,
        ).stdout.split()
        self.assertTrue(adjectives)
        self.every_candidate = [f"{adjective}-Curie" for adjective in adjectives]

    def own_every_candidate_elsewhere(self) -> None:
        """Every name the picker can produce is owned by repository B."""
        other_context = self.resolve_context(self.other)
        first = self.every_candidate[0]
        subprocess.run(
            ["/bin/bash", "-c", '. "$1"; ags_store_registration_token "$2" "$3" "$4" top-level',
             "own", str(self.install / "bin" / "lib" / "agentstack-register.sh"),
             first, "owner-token-elsewhere", other_context],
            env=self.env, capture_output=True, text=True, check=True, timeout=60,
        )
        template = json.loads((self.runtime / f"agent_owner_{first}.json").read_text())
        for name in self.every_candidate[1:]:
            record = dict(template, agent_name=name)
            if "name_key" in record:
                record["name_key"] = name.replace("-", "").casefold()
            path = self.runtime / f"agent_owner_{name}.json"
            path.write_text(json.dumps(record))
            path.chmod(0o600)
            token = self.runtime / f"agent_token_{name}"
            token.write_text("owner-token-elsewhere")
            token.chmod(0o600)
        self.assertEqual(template["project_key"], str(self.other))
        self.assertEqual(template["token_sha256"],
                         hashlib.sha256(b"owner-token-elsewhere").hexdigest())

    def test_locally_owned_preselected_name_does_not_start_the_provider(self) -> None:
        self.own_every_candidate_elsewhere()
        for provider in PROVIDERS:
            for mode in ((), ("--top-level",)):
                with self.subTest(provider=provider, mode=mode):
                    result = self.run_bootstrap(provider, *mode, self.linked)
                    detail = result.stderr[-2000:]
                    tools = [call["tool"] for call in self.mail_calls()]
                    self.assertNotIn("register_agent", tools, detail)
                    self.assertNotIn("ensure_project", tools, detail)
                    after = self.after()
                    if self.provider_ran():
                        self.assertFalse(after["agent_name"] in self.every_candidate,
                                         f"provider started as {after['agent_name']!r}, "
                                         "a name owned by another project")
                    else:
                        self.assertNotEqual(after["status"], 0, detail)

    def test_an_outage_does_not_excuse_a_locally_owned_preselected_name(self) -> None:
        self.own_every_candidate_elsewhere()
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, "--top-level", self.linked,
                                            ambient={**self.stale_foreign, "TEST_MAIL_DOWN": "1"})
                self.assertFalse(self.provider_ran(), result.stderr[-2000:])
                self.assertNotEqual(self.after()["status"], 0)

    def test_a_real_mail_outage_still_launches_with_a_safe_preselected_name(self) -> None:
        """The null case: the outage fallback this refusal must not remove."""
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, "--top-level", self.linked,
                                            ambient={**self.stale_foreign, "TEST_MAIL_DOWN": "1"})
                self.assertTrue(self.provider_ran(), result.stderr[-2000:])
                self.assertTrue(self.after()["agent_name"] in self.every_candidate,
                                self.after()["agent_name"])
                self.assertEqual(self.after()["env"]["AGENTSTACK_PROJECT_KEY"], str(self.repo))

    # The inherited context cases are covered once by the parent class.
    for _name in [name for name in dir(Phase4BootstrapContextTests) if name.startswith("test_")]:
        locals()[_name] = None
    del _name



class Phase4BootstrapReservedProvenanceTests(Phase4BootstrapContextTests):
    """A reserved marker plus a token is not ownership (#826).

    AGENTSTACK_RESERVED_IDENTITY=1, AGENT_NAME and an owner token are all
    ambient values. Without a strong owner record or corroborated legacy child
    state for this workspace, the bootstrap refuses, including during an outage.
    """

    NAME = "Reserved-Curie"
    TOKEN = "reserved-owner-token"

    def reserved_ambient(self, *, token_in_env: bool, mail_down: bool = False) -> dict[str, str]:
        ambient = {"AGENTSTACK_RESERVED_IDENTITY": "1", "AGENT_NAME": self.NAME,
                   "PARENT_AGENT": "Parent-Bohr"}
        if token_in_env:
            ambient["CHILD_REGISTRATION_TOKEN"] = self.TOKEN
        else:
            token = self.runtime / f"agent_token_{self.NAME}"
            token.write_text(self.TOKEN)
            token.chmod(0o600)
        if mail_down:
            ambient["TEST_MAIL_DOWN"] = "1"
        return ambient

    def test_reserved_marker_and_token_without_provenance_is_refused(self) -> None:
        for provider in PROVIDERS:
            for token_in_env in (True, False):
                for mail_down in (False, True):
                    with self.subTest(provider=provider, token_in_env=token_in_env,
                                      mail_down=mail_down):
                        for leftover in self.runtime.glob(f"agent_token_{self.NAME}"):
                            leftover.unlink()
                        ambient = self.reserved_ambient(token_in_env=token_in_env,
                                                        mail_down=mail_down)
                        result = self.run_bootstrap(provider, self.linked, ambient=ambient)
                        detail = result.stderr[-2000:]
                        self.assertFalse(self.provider_ran(), detail)
                        self.assertNotEqual(self.after()["status"], 0, detail)
                        tools = [call["tool"] for call in self.mail_calls()]
                        for tool in ("whois", "ensure_project", "register_agent"):
                            self.assertNotIn(tool, tools, detail)
                        self.assertFalse((self.runtime / f"agent_owner_{self.NAME}.json").exists())
                        self.assertNotIn(self.TOKEN, result.stdout + result.stderr)

    def test_reserved_identity_with_a_strong_owner_resumes(self) -> None:
        """Control: the same reserved launch with real persisted ownership."""
        subprocess.run(
            ["/bin/bash", "-c", '. "$1"; ags_store_registration_token "$2" "$3" "$4" top-level',
             "own", str(self.install / "bin" / "lib" / "agentstack-register.sh"),
             self.NAME, self.TOKEN, self.resolve_context(self.linked)],
            env=self.env, capture_output=True, text=True, check=True, timeout=60,
        )
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                result = self.run_bootstrap(provider, self.linked,
                                            ambient=self.reserved_ambient(token_in_env=False))
                detail = result.stderr[-2000:]
                self.assertTrue(self.provider_ran(), detail)
                after = self.after()
                self.assertEqual(after["agent_name"], self.NAME)
                self.assertEqual(after["env"]["AGENTSTACK_PROJECT_KEY"], str(self.repo))
                self.assertEqual(after["env"]["AGENTSTACK_PROTECTED_ROOTS"], str(self.linked))
                register = [c for c in self.mail_calls() if c["tool"] == "register_agent"]
                self.assertTrue(register, detail)
                self.assertEqual(register[-1]["args"]["name"], self.NAME)
                self.assertEqual(register[-1]["args"]["project_key"], str(self.repo))
                self.assertTrue(register[-1]["token_sent"])
                self.assertNotIn(self.TOKEN, result.stdout + result.stderr)

    for _name in [name for name in dir(Phase4BootstrapContextTests) if name.startswith("test_")]:
        locals()[_name] = None
    del _name

if __name__ == "__main__":
    unittest.main()
