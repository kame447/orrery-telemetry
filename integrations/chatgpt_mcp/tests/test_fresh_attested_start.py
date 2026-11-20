"""Isolated LaunchAgent bootstrap tests: all OS and network actions are mocked."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp import fresh_attested_start as start_module
from orrery_chatgpt_mcp.fresh_attested_start import FreshStartError


class FixturePlan:
    label = "org.agentstack.chatgpt-mcp-fresh.fixture"
    port = 49152
    dashboard_url = "http://127.0.0.1:49153"


class StartTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = patch.object(start_module, "_snapshot", return_value=(
            Path("/tmp/org.agentstack.chatgpt-mcp-fresh.fixture.plist"), "a" * 64,
        ))
        self.snapshot_mock = self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        self.live = patch.object(start_module, "check_running", return_value={
            "verified": True, "pid": 456,
        })
        self.live_mock = self.live.start()
        self.addCleanup(self.live.stop)
        self.plan = FixturePlan()
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        self.is_loaded = Mock(side_effect=[False, True])
        self.loaded_path = Mock(return_value="/tmp/org.agentstack.chatgpt-mcp-fresh.fixture.plist")
        self.port_available = Mock(return_value=True)
        self.config = dict(
            verify_release=Mock(), read_release=Mock(), read_secret=Mock(), probe=Mock(),
            is_loaded=self.is_loaded, loaded_plist_path=self.loaded_path,
            fixture_mode=True, runner=self.runner, port_available=self.port_available,
        )
        self.token = start_module.preview(
            self.plan, None, Path("/tmp"), verify_release=Mock(), read_release=Mock(),
        )["start_approval_digest"]
        self.snapshot_mock.reset_mock()

    def launch(self, token=None):
        return start_module.start(
            self.plan, None, Path("/tmp"), self.token if token is None else token,
            **self.config,
        )

    def test_preview_is_read_only(self):
        output = start_module.preview(
            self.plan, None, Path("/tmp"), verify_release=Mock(), read_release=Mock(),
        )
        self.assertFalse(output["service_started"])
        self.assertFalse(output["live_process_verified"])
        self.runner.assert_not_called()

    def test_approved_start_checks_real_attested_result(self):
        result = self.launch()
        self.assertEqual(result["pid"], 456)
        self.assertTrue(result["live_process_verified"])
        self.assertFalse(result["runtime_import_provenance_verified"])
        self.assertFalse(result["agent_processing_observed"])
        self.assertEqual(self.runner.call_count, 2)
        self.assertEqual(self.runner.call_args_list[0].args[0][:2], ["/bin/launchctl", "bootstrap"])
        self.assertEqual(self.runner.call_args_list[1].args[0][:2], ["/bin/launchctl", "kickstart"])
        self.live_mock.assert_called_once()

    def test_nonfixture_start_is_blocked(self):
        self.config["fixture_mode"] = False
        with self.assertRaisesRegex(FreshStartError, "FIXTURE_ONLY"):
            self.launch()
        self.runner.assert_not_called()

    def test_nonfixture_label_is_blocked(self):
        self.plan.label = "org.agentstack.chatgpt-mcp-fresh"
        with self.assertRaisesRegex(FreshStartError, "FIXTURE_ONLY"):
            self.launch()
        self.runner.assert_not_called()

    def test_near_match_fixture_prefix_is_not_authorized(self):
        self.plan.label = "org.agentstack.chatgpt-mcp-fresh.fixtureevil"
        with self.assertRaisesRegex(FreshStartError, "FIXTURE_ONLY"):
            self.launch()
        self.runner.assert_not_called()

    def test_default_dashboard_refused_before_any_launchctl(self):
        self.plan.dashboard_url = "http://127.0.0.1:8770"
        with self.assertRaisesRegex(FreshStartError, "DASHBOARD_NOT_ISOLATED"):
            self.launch()
        self.runner.assert_not_called()
        self.is_loaded.assert_not_called()

    def test_wrong_digest_blocks_mutation(self):
        with self.assertRaisesRegex(FreshStartError, "APPROVAL_MISMATCH"):
            self.launch("f" * 64)
        self.runner.assert_not_called()

    def test_loaded_or_unknown_prestate_blocks_mutation(self):
        for state in (True, None):
            with self.subTest(state=state):
                self.is_loaded.side_effect = None
                self.is_loaded.return_value = state
                with self.assertRaisesRegex(FreshStartError, "LOADED_OR_UNKNOWN"):
                    self.launch()
        self.runner.assert_not_called()

    def test_port_unavailable_blocks_mutation(self):
        self.port_available.return_value = False
        with self.assertRaisesRegex(FreshStartError, "PORT_OCCUPIED_OR_UNKNOWN"):
            self.launch()
        self.runner.assert_not_called()

    def test_source_change_before_launch_blocks_mutation(self):
        self.snapshot_mock.side_effect = [
            (Path("/tmp/staged.plist"), "a" * 64),
            (Path("/tmp/staged.plist"), "b" * 64),
        ]
        with self.assertRaisesRegex(FreshStartError, "SOURCE_CHANGED"):
            self.launch()
        self.runner.assert_not_called()

    def test_bootstrap_failure_does_not_retry_or_rollback(self):
        self.runner.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(FreshStartError, "OUTCOME_UNKNOWN"):
            self.launch()
        self.assertEqual(self.runner.call_count, 1)
        self.live_mock.assert_not_called()

    def test_timeout_does_not_retry_or_rollback(self):
        self.runner.side_effect = subprocess.TimeoutExpired("launchctl", 15)
        with self.assertRaisesRegex(FreshStartError, "OUTCOME_UNKNOWN"):
            self.launch()
        self.assertEqual(self.runner.call_count, 1)
        self.live_mock.assert_not_called()

    def test_unverified_loaded_job_cannot_kickstart(self):
        self.is_loaded.side_effect = [False, None]
        with self.assertRaisesRegex(FreshStartError, "OUTCOME_UNKNOWN"):
            self.launch()
        self.assertEqual(self.runner.call_count, 1)

    def test_different_plist_cannot_kickstart(self):
        self.loaded_path.return_value = "/tmp/other.plist"
        with self.assertRaisesRegex(FreshStartError, "OUTCOME_UNKNOWN"):
            self.launch()
        self.assertEqual(self.runner.call_count, 1)

    def test_kickstart_failure_never_boots_out(self):
        self.runner.side_effect = [
            subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1),
        ]
        with self.assertRaisesRegex(FreshStartError, "OUTCOME_UNKNOWN"):
            self.launch()
        self.assertEqual(self.runner.call_count, 2)
        self.live_mock.assert_not_called()

    def test_attestation_refusal_never_claims_service_started(self):
        self.live_mock.return_value = {"verified": False}
        with self.assertRaisesRegex(FreshStartError, "OUTCOME_UNKNOWN"):
            self.launch()
        self.assertEqual(self.runner.call_count, 2)

    def test_probe_failure_redacts_sensitive_error(self):
        self.live_mock.side_effect = RuntimeError("private secret and local path")
        with self.assertRaises(FreshStartError) as caught:
            self.launch()
        self.assertEqual(str(caught.exception), "FRESH_START_OUTCOME_UNKNOWN")
        self.assertEqual(self.runner.call_count, 2)


if __name__ == "__main__":
    unittest.main()
