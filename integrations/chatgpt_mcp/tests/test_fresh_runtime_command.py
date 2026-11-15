"""Offline contract for a same-process pinned runtime; never starts launchd/Mail."""
from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp.fresh_import_attest import ImportPlan
from orrery_chatgpt_mcp.fresh_release_plan import Plan
from orrery_chatgpt_mcp.fresh_runtime_command import (
    RuntimeCommandError, _server_source, build_command,
)


class RuntimeCommandTests(unittest.TestCase):
    def setUp(self):
        self.plan = Plan(
            python=Path("/private/tmp/mcp-test/venv/bin/python"),
            release_dir=Path("/private/tmp/mcp-test/release"),
            release_digest="a" * 64, source_commit="b" * 40,
            state_dir=Path("/private/tmp/mcp-test/state"),
            policy_path=Path("/private/tmp/mcp-test/policy.json"),
            label="org.agentstack.chatgpt-mcp-fresh.fixture",
            port=8792, mail_instance_id="1" * 32, sender_agent_id=7,
            recipients=((8, "AgentTwo", "to"),),
        )
        self.dependency = ImportPlan(
            mail_package=Path("/private/tmp/mcp-test/mail/agentstack_mail"),
            mail_digest="c" * 64,
            site_packages=Path("/private/tmp/mcp-test/pinned-site"),
            dependency_manifest=Path("/private/tmp/mcp-test/pin.json"),
        )
        self.verify_release = Mock()
        self.read_release = Mock()
        self.evidence = {
            "validated": True, "third_party_pinned": True,
            "import_origins_verified": True, "receipt_contract": True,
        }
        pin = patch(
            "orrery_chatgpt_mcp.fresh_dependency_attest.verify",
            return_value={"site_packages_sha256": "d" * 64},
        )
        check = patch(
            "orrery_chatgpt_mcp.fresh_runtime_command.check_imports",
            return_value=self.evidence,
        )
        self.pin = pin.start()
        self.check = check.start()
        self.addCleanup(pin.stop)
        self.addCleanup(check.stop)

    def build(self, **kwargs):
        return build_command(
            self.plan, kwargs.pop("dependency", self.dependency),
            verify_release=self.verify_release, read_release=self.read_release,
        )

    def test_same_interpreter_checks_before_main(self):
        command = self.build()
        argv = command.argv
        self.assertEqual(argv[:5], (
            str(self.plan.python), "-I", "-S", "-B", "-c",
        ))
        self.assertEqual(argv[6:], (
            str(self.plan.release_dir), self.plan.release_digest,
            str(self.dependency.mail_package), self.dependency.mail_digest,
            str(self.dependency.site_packages),
            str(self.dependency.dependency_manifest), "d" * 64,
        ))
        self.assertIn("gateway.main(['serve'])", argv[5])
        self.assertLess(
            argv[5].index("proof = verify(pin_manifest, site)", argv[5].index("try:\n    result = gateway.main"),
        )
        self.assertLess(
            argv[5].rindex("proof = verify(pin_manifest, site)"),
            argv[5].index("gateway.main(['serve'])"),
        )
        compile(argv[5], "isolated-fresh-server", "exec")
        self.assertTrue(command.source_attested_before_serve)
        self.assertFalse(command.service_started)

    def test_env_has_no_inherited_credentials_or_user_site(self):
        with patch.dict(
            "os.environ",
            {"AGENTSTACK_TOKEN": "private", "PYTHONPATH": "/untrusted",
             "MCP_SERVER_URL": "private-url", "HOME": "/real-home"},
        ):
            command = self.build()
        env = command.env()
        self.assertEqual(env["HOME"], "/nonexistent")
        self.assertEqual(env["ORRERY_MCP_ENABLE_SPAWN"], "0")
        self.assertEqual(env["ORRERY_MCP_ENABLE_MESSAGE"], "1")
        self.assertEqual(env["ORRERY_MCP_MODE"], "fresh")
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("AGENTSTACK_TOKEN", env)
        self.assertNotIn("MCP_SERVER_URL", env)
        self.assertNotIn("private-url", str(command))
        self.assertNotIn("private", str(command))

    def test_prepare_does_not_start_any_process(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("started")), \
             patch.object(subprocess, "run", side_effect=AssertionError("started")):
            self.build()
        self.assertEqual(self.check.call_count, 1)

    def test_rejects_unpinned_dependencies_before_any_check(self):
        other = ImportPlan(
            mail_package=self.dependency.mail_package,
            mail_digest=self.dependency.mail_digest,
            site_packages=self.dependency.site_packages,
        )
        with self.assertRaisesRegex(RuntimeCommandError, "PREPARATION_REFUSED"):
            self.build(dependency=other)
        self.check.assert_not_called()
        self.pin.assert_not_called()

    def test_rejects_incomplete_import_proof(self):
        for key in self.evidence:
            with self.subTest(key=key):
                self.check.return_value={**self.evidence,key:False}
                with self.assertRaisesRegex(RuntimeCommandError, "PREPARATION_REFUSED"):
                    self.build()
        self.pin.assert_not_called()

    def test_rejects_invalid_dependency_digest(self):
        for value in ("0"*63, True, "g"*64):
            with self.subTest(value=value):
                self.pin.return_value={"site_packages_sha256": value}
                with self.assertRaisesRegex(RuntimeCommandError, "PREPARATION_REFUSED"):
                    self.build()

    def test_probe_contract_change_fails_closed(self):
        with patch("orrery_chatgpt_mcp.fresh_runtime_command.PROBE",
                   "print('not a verified import')"):
            with self.assertRaisesRegex(RuntimeCommandError, "PROBE_CONTRACT_CHANGED"):
                _server_source()
            with self.assertRaisesRegex(RuntimeCommandError, "PROBE_CONTRACT_CHANGED"):
                self.build()

    def test_exception_does_not_leak_private_detail(self):
        self.check.side_effect=ValueError("sensitive credential and local path")
        with self.assertRaises(RuntimeCommandError) as result:
            self.build()
        self.assertEqual(str(result.exception), "FRESH_BOOTSTRAP_PREPARATION_REFUSED")
        self.assertNotIn("sensitive", repr(result.exception))


if __name__ == "__main__":
    unittest.main()
