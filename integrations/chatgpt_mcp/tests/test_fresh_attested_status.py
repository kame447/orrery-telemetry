"""Read-only attested service status; isolated fixtures never load launchd."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp.fresh_attested_stage import stage
from orrery_chatgpt_mcp.fresh_attested_status import (
    AttestedStatusError, check_running,
)
from orrery_chatgpt_mcp.fresh_import_attest import ImportPlan
from orrery_chatgpt_mcp.fresh_release_plan import Plan
from orrery_chatgpt_mcp.fresh_runtime_command import RuntimeCommand


class AttestedStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        (self.home / "Library").mkdir(mode=0o700)
        (self.home / "Library" / "LaunchAgents").mkdir(mode=0o700)
        (self.home / "state").mkdir(mode=0o700)
        self.policy_path = self.home / "policy.json"
        self.policy = {"kind": "fixture", "context_path": str(self.home / "context.json")}
        self.save_policy()
        self.plan = Plan(
            python=self.home / "python", release_dir=self.home / "release",
            release_digest="a" * 64, source_commit="b" * 40,
            state_dir=self.home / "state", policy_path=self.policy_path,
            label="org.agentstack.chatgpt-mcp-fresh.fixture", port=8792,
            mail_instance_id="1" * 32, sender_agent_id=7,
            recipients=((8, "OtherAgent", "to"),),
        )
        self.dependency = ImportPlan(
            mail_package=self.home / "mail" / "agentstack_mail",
            mail_digest="c" * 64, site_packages=self.home / "site",
            dependency_manifest=self.home / "pin.json",
        )
        args = (
            str(self.plan.python), "-I", "-S", "-B", "-c", "verified-script",
            str(self.plan.release_dir), self.plan.release_digest,
            str(self.dependency.mail_package), self.dependency.mail_digest,
            str(self.dependency.site_packages), str(self.dependency.dependency_manifest),
            "d" * 64,
        )
        env = {
            "PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
            "PYTHONDONTWRITEBYTECODE": "1",
            "ORRERY_MCP_MODE": "fresh", "ORRERY_MCP_AUTH": "pairing",
            "ORRERY_MCP_ENABLE_MESSAGE": "1", "ORRERY_MCP_ENABLE_SPAWN": "0",
            "ORRERY_MCP_PORT": str(self.plan.port),
            "ORRERY_MCP_STATE_DIR": str(self.plan.state_dir),
            "ORRERY_MCP_FRESH_MESSAGE_POLICY": str(self.plan.policy_path),
            "ORRERY_MCP_DASHBOARD_URL": "http://127.0.0.1:8770",
        }
        cmd = RuntimeCommand(args, tuple(sorted(env.items())), True)
        build = patch("orrery_chatgpt_mcp.fresh_attested_stage.build_command",
                      return_value=cmd)
        build.start()
        self.addCleanup(build.stop)
        self.read_release = Mock()
        self.verify_release = Mock()
        self.read_secret = Mock(return_value="a" * 43)
        self.live = {
            "verified": True, "pid": 12345, "runtime_mode": "fresh",
            "mail_instance_id": self.plan.mail_instance_id,
            "sender_agent_id": self.plan.sender_agent_id,
            "recipient_count": 1, "message_enabled_on_server": True,
            "spawn_enabled_on_server": False, "tools_verified": 6,
        }
        self.probe = Mock(side_effect=lambda **kw: dict(self.live))
        from orrery_chatgpt_mcp.fresh_attested_stage import preview
        approval = preview(self.plan, self.dependency, self.home,
                           verify_release=self.verify_release,
                           read_release=self.read_release)["approval_digest"]
        stage(self.plan, self.dependency, self.home, approval,
              verify_release=self.verify_release, read_release=self.read_release,
              is_loaded=lambda label: False)
        self.plist = self.home / "Library" / "LaunchAgents" / (self.plan.label + ".plist")

    def save_policy(self):
        self.policy_path.write_text(json.dumps(self.policy))
        self.policy_path.chmod(0o600)

    def check(self):
        return check_running(
            self.plan, self.dependency, self.home,
            verify_release=self.verify_release, read_release=self.read_release,
            read_secret=self.read_secret, probe=self.probe,
        )

    def test_verified_effective_authority_without_import_claim(self):
        result = self.check()
        self.assertEqual(result["pid"], 12345)
        self.assertTrue(result["effective_authority_verified"])
        self.assertFalse(result["runtime_import_provenance_verified"])
        self.assertFalse(result["delivery_observed"])
        self.assertFalse(result["agent_receive_observed"])
        self.assertFalse(result["agent_processing_observed"])
        self.assertNotIn("a" * 43, repr(result))
        self.assertNotIn(str(self.home), repr(result))
        kwargs = self.probe.call_args.kwargs
        self.assertEqual(kwargs["pairing_secret"], "a" * 43)
        self.assertEqual(kwargs["plist_path"], str(self.plist))
        self.assertEqual(kwargs["expected"].recipients, self.plan.recipients)

    def test_missing_plist_cannot_probe(self):
        self.plist.unlink()
        with self.assertRaisesRegex(AttestedStatusError, "PLIST_UNVERIFIED"):
            self.check()
        self.probe.assert_not_called()
        self.read_secret.assert_not_called()

    def test_rejects_changed_plist_bytes_even_when_semantics_equivalent(self):
        self.plist.write_bytes(self.plist.read_bytes() + b"\n")
        with self.assertRaisesRegex(AttestedStatusError, "PLIST_UNVERIFIED"):
            self.check()
        self.probe.assert_not_called()

    def test_rejects_group_readable_plist(self):
        self.plist.chmod(0o640)
        with self.assertRaisesRegex(AttestedStatusError, "PLIST_UNVERIFIED"):
            self.check()
        self.probe.assert_not_called()

    def test_rejects_symlink_plist(self):
        other = self.home / "other.plist"
        self.plist.replace(other)
        self.plist.symlink_to(other)
        with self.assertRaisesRegex(AttestedStatusError, "PLIST_UNVERIFIED"):
            self.check()
        self.probe.assert_not_called()

    def test_rejects_untrusted_secret_without_network_probe(self):
        for bad in (None, False, "short", "x" * 42):
            with self.subTest(bad=bad):
                self.read_secret.return_value = bad
                with self.assertRaisesRegex(AttestedStatusError, "PAIRING_UNVERIFIED"):
                    self.check()
        self.probe.assert_not_called()

    def test_rejects_false_positive_or_wrong_live_authority(self):
        for key, bad in (
            ("verified", 1), ("pid", True), ("pid", 1),
            ("runtime_mode", "legacy"), ("mail_instance_id", "f" * 32),
            ("sender_agent_id", 8), ("recipient_count", True),
            ("message_enabled_on_server", False),
            ("spawn_enabled_on_server", True), ("tools_verified", 4),
        ):
            with self.subTest(key=key, bad=bad):
                prior = self.live[key]
                self.live[key] = bad
                with self.assertRaisesRegex(AttestedStatusError, "LIVE_AUTHORITY_UNVERIFIED"):
                    self.check()
                self.live[key] = prior

    def test_read_failure_or_probe_timeout_redacts_details(self):
        self.probe.side_effect = TimeoutError("local secret and path")
        with self.assertRaises(AttestedStatusError) as caught:
            self.check()
        self.assertEqual(str(caught.exception), "FRESH_ATTESTED_STATUS_UNVERIFIED")

    def test_rejects_policy_modification_while_probing(self):
        def change_policy(**kw):
            self.policy["context_path"] = "/different"
            self.save_policy()
            return dict(self.live)
        self.probe.side_effect = change_policy
        with self.assertRaisesRegex(AttestedStatusError, "SOURCE_CHANGED"):
            self.check()

    def test_rejects_plist_modification_while_probing(self):
        def change_plist(**kw):
            self.plist.write_bytes(b"race")
            return dict(self.live)
        self.probe.side_effect = change_plist
        with self.assertRaisesRegex(AttestedStatusError, "PLIST_UNVERIFIED"):
            self.check()

    def test_does_not_mutate_plist_on_success(self):
        old = self.plist.read_bytes()
        self.check()
        self.assertEqual(self.plist.read_bytes(), old)


if __name__ == "__main__":
    unittest.main()
