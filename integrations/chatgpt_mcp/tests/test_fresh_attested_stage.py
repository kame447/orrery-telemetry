"""Isolated LaunchAgent staging; no real launchctl, Mail, or service."""
from dataclasses import replace
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp.fresh_import_attest import ImportPlan
from orrery_chatgpt_mcp.fresh_release_plan import Plan
from orrery_chatgpt_mcp.fresh_runtime_command import RuntimeCommand
from orrery_chatgpt_mcp.fresh_attested_stage import (
    AttestedStageError, preview, stage,
)
from orrery_chatgpt_mcp.fresh_service_status import _read_plist


class AttestedStageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        (self.home / "Library").mkdir(mode=0o700)
        (self.home / "Library" / "LaunchAgents").mkdir(mode=0o700)
        (self.home / "state").mkdir(mode=0o700)
        self.policy = self.home / "policy.json"
        self.policy_content = {"kind": "fixed", "context_path": str(self.home / "context.json")}
        self.save_policy()
        self.plan = Plan(
            python=self.home / "python",
            release_dir=self.home / "release",
            release_digest="a" * 64,
            source_commit="b" * 40,
            state_dir=self.home / "state",
            policy_path=self.policy,
            label="org.agentstack.chatgpt-mcp-fresh.fixture",
            port=8792,
            mail_instance_id="1" * 32,
            sender_agent_id=7,
            recipients=((8, "AgentTwo", "to"),),
        )
        self.dependency = ImportPlan(
            mail_package=self.home / "mail" / "agentstack_mail",
            mail_digest="c" * 64,
            site_packages=self.home / "site",
            dependency_manifest=self.home / "pin.json",
        )
        self.verify_release = Mock()
        self.read_release = Mock()
        self.is_loaded = Mock(return_value=False)
        self.command = self.make_command()
        p = patch(
            "orrery_chatgpt_mcp.fresh_attested_stage.build_command",
            return_value=self.command,
        )
        self.build = p.start()
        self.addCleanup(p.stop)

    def save_policy(self):
        self.policy.write_text(json.dumps(self.policy_content))
        self.policy.chmod(0o600)

    def make_command(self, plan=None, dependency=None):
        plan = plan or self.plan
        dependency = dependency or self.dependency
        args = (
            str(plan.python), "-I", "-S", "-B", "-c", "verified_code_here",
            str(plan.release_dir), plan.release_digest,
            str(dependency.mail_package), dependency.mail_digest,
            str(dependency.site_packages), str(dependency.dependency_manifest),
            "d" * 64,
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/nonexistent",
            "PYTHONDONTWRITEBYTECODE": "1",
            "ORRERY_MCP_MODE": "fresh",
            "ORRERY_MCP_AUTH": "pairing",
            "ORRERY_MCP_ENABLE_MESSAGE": "1",
            "ORRERY_MCP_ENABLE_SPAWN": "0",
            "ORRERY_MCP_PORT": str(plan.port),
            "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
            "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(plan.policy_path),
            "ORRERY_MCP_DASHBOARD_URL": "http://127.0.0.1:8770",
        }
        return RuntimeCommand(args, tuple(sorted(env.items())), True)

    @property
    def target(self):
        return self.home / "Library" / "LaunchAgents" / (self.plan.label + ".plist")

    def show(self, **kwargs):
        return preview(kwargs.get("plan", self.plan),
                       kwargs.get("dependency", self.dependency),
                       kwargs.get("home", self.home),
                       verify_release=self.verify_release,
                       read_release=self.read_release)

    def apply(self, digest=None, **kwargs):
        return stage(kwargs.get("plan", self.plan),
                     kwargs.get("dependency", self.dependency),
                     kwargs.get("home", self.home),
                     self.show()["approval_digest"] if digest is None else digest,
                     verify_release=self.verify_release,
                     read_release=self.read_release,
                     is_loaded=self.is_loaded)

    def test_preview_is_inactive_no_secret_or_argv_in_receipt(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("started")), \
             patch.object(subprocess, "run", side_effect=AssertionError("started")):
            result = self.show()
        self.assertEqual(result, self.show())
        self.assertFalse(self.target.exists())
        self.is_loaded.assert_not_called()
        self.assertFalse(result["process_started"])
        self.assertFalse(result["live_process_verified"])
        self.assertTrue(result["same_process_import_preflight"])
        self.assertNotIn(str(self.home), repr(result))
        self.assertNotIn("verified_code_here", repr(result))

    def test_writes_exact_inactive_attested_command_without_loading(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("started")), \
             patch.object(subprocess, "run", side_effect=AssertionError("started")):
            result = self.apply()
        self.assertEqual(result["operation"], "attested_staged_inactive")
        self.assertFalse(result["process_started"])
        self.assertEqual(self.is_loaded.call_count, 1)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        plist = _read_plist(self.target)
        self.assertEqual(plist["ProgramArguments"], list(self.command.argv))
        self.assertEqual(plist["EnvironmentVariables"], self.command.env())
        self.assertFalse(plist["RunAtLoad"])
        self.assertFalse(plist["KeepAlive"])
        self.assertEqual(plist["Umask"], 0o077)
        self.assertNotIn("PYTHONPATH", plist["EnvironmentVariables"])
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_approval_replay_rejected_after_policy_change(self):
        old = self.show()["approval_digest"]
        self.policy_content["context_path"] = str(self.home / "other-context")
        self.save_policy()
        with self.assertRaisesRegex(AttestedStageError, "APPROVAL_MISMATCH"):
            self.apply(old)
        self.assertFalse(self.target.exists())

    def test_changed_authority_or_release_changes_approval(self):
        old = self.show()["approval_digest"]
        for plan in (
            replace(self.plan, source_commit="f" * 40),
            replace(self.plan, recipients=((9, "Other", "to"),)),
            replace(self.plan, release_digest="0" * 64),
        ):
            with self.subTest(plan=plan):
                with self.assertRaisesRegex(AttestedStageError, "APPROVAL_MISMATCH|COMMAND_UNVERIFIED"):
                    self.apply(old, plan=plan)
        self.assertFalse(self.target.exists())

    def test_changed_script_changes_digest(self):
        original = self.show()["approval_digest"]
        self.build.return_value = replace(
            self.command,
            argv=self.command.argv[:5] + ("altered-script",) + self.command.argv[6:],
        )
        self.assertNotEqual(original, self.show()["approval_digest"])
        with self.assertRaisesRegex(AttestedStageError, "APPROVAL_MISMATCH"):
            self.apply(original)

    def test_rejects_bad_command_contracts(self):
        invalid = (
            replace(self.command, requires_in_process_attestation=False),
            replace(self.command, service_started=True),
            replace(self.command, argv=(str(self.plan.python), "-m", "server")),
            replace(self.command, environment=self.command.environment + (("PYTHONPATH", "/tmp"),)),
            replace(self.command, environment=tuple((k, "1" if k == "ORRERY_MCP_ENABLE_SPAWN" else v)
                                                    for k, v in self.command.environment)),
        )
        for command in invalid:
            with self.subTest(command=command):
                self.build.return_value = command
                with self.assertRaisesRegex(AttestedStageError, "COMMAND_UNVERIFIED"):
                    self.show()
        self.assertFalse(self.target.exists())

    def test_existing_file_is_never_replaced(self):
        self.target.write_bytes(b"keep-old-service")
        with self.assertRaisesRegex(AttestedStageError, "TARGET_EXISTS"):
            self.apply()
        self.assertEqual(self.target.read_bytes(), b"keep-old-service")

    def test_symlink_existing_target_is_not_replaced(self):
        outside = self.home / "private"
        outside.write_bytes(b"unchanged")
        self.target.symlink_to(outside)
        with self.assertRaisesRegex(AttestedStageError, "TARGET_EXISTS"):
            self.apply()
        self.assertEqual(outside.read_bytes(), b"unchanged")

    def test_loaded_or_unknown_label_rejected(self):
        for outcome in (True, None, 1):
            with self.subTest(outcome=outcome):
                self.is_loaded.return_value = outcome
                with self.assertRaisesRegex(AttestedStageError, "LOADED_OR_UNKNOWN"):
                    self.apply()
        self.assertFalse(self.target.exists())

    def test_launchctl_error_does_not_leak_details(self):
        self.is_loaded.side_effect = TimeoutError("secret local process details")
        with self.assertRaises(AttestedStageError) as raised:
            self.apply()
        self.assertEqual(str(raised.exception), "FRESH_ATTESTED_STAGE_LOADED_OR_UNKNOWN")
        self.assertFalse(self.target.exists())

    def test_concurrent_creator_wins(self):
        def collision(src, dest):
            Path(dest).write_bytes(b"racing-operator")
            raise FileExistsError("secret")
        with patch("orrery_chatgpt_mcp.fresh_attested_stage.os.link", side_effect=collision):
            with self.assertRaisesRegex(AttestedStageError, "WRITE_UNCERTAIN"):
                self.apply()
        self.assertEqual(self.target.read_bytes(), b"racing-operator")
        self.assertEqual(len(list(self.target.parent.iterdir())), 1)

    def test_uncertain_directory_fsync_keeps_published_file(self):
        import stat
        real_fsync = os.fsync

        def refuse_dir_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("untrusted error details")
            return real_fsync(fd)

        with patch("orrery_chatgpt_mcp.fresh_attested_stage.os.fsync", side_effect=refuse_dir_sync):
            with self.assertRaisesRegex(AttestedStageError, "WRITE_UNCERTAIN"):
                self.apply()
        self.assertEqual(_read_plist(self.target)["Label"], self.plan.label)
        with self.assertRaisesRegex(AttestedStageError, "TARGET_EXISTS"):
            self.apply()

    def test_unsafe_launchagents_directory_rejected(self):
        self.target.parent.chmod(0o777)
        with self.assertRaisesRegex(AttestedStageError, "PREFLIGHT_REFUSED"):
            self.show()
        self.is_loaded.assert_not_called()

    def test_dependencies_must_pass_preparation(self):
        self.build.side_effect = ValueError("private credential")
        with self.assertRaises(AttestedStageError) as raised:
            self.show()
        self.assertEqual(str(raised.exception), "FRESH_ATTESTED_STAGE_PREFLIGHT_REFUSED")

    def test_policy_race_during_preflight_rejected(self):
        original = self.build.side_effect
        def mutation(*args, **kwargs):
            self.policy_content["context_path"] = str(self.home / "racing-change")
            self.save_policy()
            return self.command
        self.build.side_effect = mutation
        with self.assertRaisesRegex(AttestedStageError, "POLICY_CHANGED"):
            self.show()
        self.build.side_effect = original


if __name__ == "__main__":
    unittest.main()
