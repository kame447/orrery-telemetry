"""Read-only recovery contract with fake launchd and PID observations."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp import fresh_fixture_recovery as recovery
from orrery_chatgpt_mcp.fresh_fixture_recovery import FreshRecoveryError


class Plan:
    label = "org.agentstack.chatgpt-mcp-fresh.fixture"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.plan = Plan()
        self.plan.label = Plan.label
        self.snapshot_patch = patch.object(
            recovery, "_snapshot", return_value=(Path("/tmp/fixture.plist"), "a" * 64),
        )
        self.snapshot = self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.check_patch = patch.object(
            recovery, "check_running", return_value={"verified": True, "pid": 321},
        )
        self.check = self.check_patch.start()
        self.addCleanup(self.check_patch.stop)
        self.loaded = Mock(return_value=True)
        self.path = Mock(return_value="/tmp/fixture.plist")
        self.alive = Mock(return_value=False)
        self.secret = Mock()
        self.kwargs = dict(
            verify_release=Mock(), read_release=Mock(), read_secret=self.secret,
            probe=Mock(), is_loaded=self.loaded, loaded_plist_path=self.path,
            fixture_mode=True, pid_alive=self.alive,
        )

    def examine(self, **updates):
        return recovery.inspect(self.plan, None, Path("/tmp"), **(self.kwargs | updates))

    def test_live_attestation_is_observation_not_permission(self):
        result = self.examine(previous_pid=321)
        self.assertEqual(result["state"], "running_attested")
        self.assertEqual(result["verified_pid"], 321)
        self.assertTrue(result["same_as_previous_pid"])
        self.assertFalse(result["restart_authorized"])
        self.assertFalse(result["stop_authorized"])
        self.assertFalse(result["mail_delivery_observed"])
        self.alive.assert_not_called()

    def test_replaced_live_pid_is_not_assumed_original(self):
        result = self.examine(previous_pid=987)
        self.assertFalse(result["same_as_previous_pid"])
        self.assertFalse(result["service_recovered"])

    def test_unloaded_without_old_pid_does_not_claim_stop(self):
        self.loaded.return_value = False
        result = self.examine()
        self.assertEqual(result["state"], "job_unloaded_observed")
        self.assertFalse(result["previous_pid_absent_observed"])
        self.assertFalse(result["restart_authorized"])
        self.check.assert_not_called()
        self.secret.assert_not_called()

    def test_old_pid_absent_observed_but_no_automatic_restart(self):
        self.loaded.return_value = False
        result = self.examine(previous_pid=321)
        self.assertTrue(result["previous_pid_absent_observed"])
        self.assertFalse(result["restart_authorized"])
        self.assertEqual(self.alive.call_count, 2)

    def test_old_pid_still_alive_rejected(self):
        self.loaded.return_value = False
        self.alive.return_value = True
        with self.assertRaisesRegex(FreshRecoveryError, "PREVIOUS_PID_UNVERIFIED"):
            self.examine(previous_pid=321)

    def test_old_pid_unknown_rejected(self):
        self.loaded.return_value = False
        self.alive.return_value = None
        with self.assertRaisesRegex(FreshRecoveryError, "PREVIOUS_PID_UNVERIFIED"):
            self.examine(previous_pid=321)

    def test_invalid_old_pid_type_rejected(self):
        for invalid in (True, 0, -1, "321", 1):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(FreshRecoveryError, "INVALID_PREVIOUS_PID"):
                    self.examine(previous_pid=invalid)
        self.snapshot.assert_not_called()

    def test_explicit_fixture_scope(self):
        with self.assertRaisesRegex(FreshRecoveryError, "FIXTURE_ONLY"):
            self.examine(fixture_mode=False)
        self.plan.label = "org.agentstack.chatgpt-mcp-fresh.fixtureevil"
        with self.assertRaisesRegex(FreshRecoveryError, "FIXTURE_ONLY"):
            self.examine()
        self.snapshot.assert_not_called()

    def test_unknown_loaded_state_refused(self):
        self.loaded.return_value = None
        with self.assertRaisesRegex(FreshRecoveryError, "JOB_UNKNOWN"):
            self.examine()

    def test_wrong_plist_refused(self):
        self.path.return_value = "/tmp/foreign.plist"
        with self.assertRaisesRegex(FreshRecoveryError, "LOADED_SOURCE_UNVERIFIED"):
            self.examine()
        self.check.assert_not_called()

    def test_unverified_running_pid_refused(self):
        self.check.return_value = {"verified": False, "pid": 321}
        with self.assertRaisesRegex(FreshRecoveryError, "PROCESS_UNVERIFIED"):
            self.examine()

    def test_loaded_state_changed_mid_read_refused(self):
        self.loaded.side_effect = [True, False]
        with self.assertRaisesRegex(FreshRecoveryError, "STATE_CHANGED"):
            self.examine()

    def test_source_changed_mid_read_refused(self):
        self.snapshot.side_effect = [(Path("/tmp/fixture.plist"), "a" * 64),
                                     (Path("/tmp/fixture.plist"), "b" * 64)]
        with self.assertRaisesRegex(FreshRecoveryError, "STATE_CHANGED"):
            self.examine()

    def test_old_pid_reused_mid_read_refused(self):
        self.loaded.return_value = False
        self.alive.side_effect = [False, True]
        with self.assertRaisesRegex(FreshRecoveryError, "STATE_CHANGED"):
            self.examine(previous_pid=321)

    def test_probe_errors_do_not_leak_secrets(self):
        self.check.side_effect = RuntimeError("private pairing token")
        with self.assertRaises(FreshRecoveryError) as err:
            self.examine()
        self.assertEqual(str(err.exception), "FRESH_RECOVERY_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
