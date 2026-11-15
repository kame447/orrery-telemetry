"""Fixture LaunchAgent shutdown contract: OS and network operations mocked."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp import fresh_attested_stop as module
from orrery_chatgpt_mcp.fresh_attested_stop import FreshStopError


class Plan:
    label = "org.agentstack.chatgpt-mcp-fresh.fixture"
    port = 49152


class StopTests(unittest.TestCase):
    def setUp(self):
        self.plan = Plan()
        self.plan.label = "org.agentstack.chatgpt-mcp-fresh.fixture"
        self.snapshot_patch = patch.object(module, "_snapshot", return_value=(
            Path("/tmp/org.agentstack.chatgpt-mcp-fresh.fixture.plist"), "a" * 64))
        self.snapshot = self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.live_patch = patch.object(module, "check_running", return_value={
            "verified": True, "pid": 456})
        self.live = self.live_patch.start()
        self.addCleanup(self.live_patch.stop)
        self.loaded = Mock(return_value=True)
        self.plist = Mock(return_value="/tmp/org.agentstack.chatgpt-mcp-fresh.fixture.plist")
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        self.pid_alive = Mock(side_effect=[True, False])
        self.kwargs = dict(
            verify_release=Mock(), read_release=Mock(), read_secret=Mock(),
            probe=Mock(), is_loaded=self.loaded, loaded_plist_path=self.plist,
            fixture_mode=True, runner=self.runner, pid_alive=self.pid_alive,
        )
        self.token = module.preview(
            self.plan, None, Path("/tmp"), **{k: v for k, v in self.kwargs.items()
                if k not in ("runner", "pid_alive")})["stop_approval_digest"]
        self.snapshot.reset_mock()
        self.live.reset_mock()
        self.loaded.reset_mock()
        self.plist.reset_mock()

    def shutdown(self, token=None):
        return module.stop(self.plan, None, Path("/tmp"),
                           self.token if token is None else token, **self.kwargs)

    def test_preview_is_read_only_and_binds_pid(self):
        p = module.preview(
            self.plan, None, Path("/tmp"), **{k: v for k, v in self.kwargs.items()
                if k not in ("runner", "pid_alive")})
        self.assertEqual(p["verified_pid"], 456)
        self.assertFalse(p["job_unloaded"])
        self.runner.assert_not_called()

    def test_stop_boots_out_one_exact_label(self):
        self.loaded.side_effect = [True] * 4 + [False]
        out = self.shutdown()
        self.assertTrue(out["job_unloaded_verified"])
        self.assertTrue(out["previous_pid_exited_verified"])
        self.assertEqual(out["previous_pid"], 456)
        self.assertFalse(out["automatic_restart_attempted"])
        self.runner.assert_called_once()
        argv = self.runner.call_args.args[0]
        self.assertEqual(argv[:2], ["/bin/launchctl", "bootout"])
        self.assertRegex(argv[-1], r"^gui/[0-9]+/org\.agentstack\.chatgpt-mcp-fresh\.fixture$")

    def test_wrong_approval_never_mutates(self):
        with self.assertRaisesRegex(FreshStopError, "APPROVAL_MISMATCH"):
            self.shutdown("f" * 64)
        self.runner.assert_not_called()

    def test_fixture_explicit_optin(self):
        self.kwargs["fixture_mode"] = False
        with self.assertRaisesRegex(FreshStopError, "FIXTURE_ONLY"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_near_match_fixture_label_rejected(self):
        self.plan.label = "org.agentstack.chatgpt-mcp-fresh.fixtureevil"
        with self.assertRaisesRegex(FreshStopError, "FIXTURE_ONLY"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_loaded_unknown_or_false_rejected(self):
        for state in (None, False):
            with self.subTest(state=state):
                self.loaded.return_value = state
                with self.assertRaisesRegex(FreshStopError, "JOB_UNVERIFIED"):
                    self.shutdown()
        self.runner.assert_not_called()

    def test_different_loaded_plist_refused(self):
        self.plist.return_value = "/tmp/unrelated.plist"
        with self.assertRaisesRegex(FreshStopError, "JOB_UNVERIFIED"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_unverified_running_process_refused(self):
        self.live.return_value = {"verified": False}
        with self.assertRaisesRegex(FreshStopError, "PROCESS_UNVERIFIED"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_changed_pid_before_mutation_refused(self):
        self.live.side_effect = [
            {"verified": True, "pid": 456}, {"verified": True, "pid": 789},
        ]
        with self.assertRaisesRegex(FreshStopError, "TARGET_CHANGED"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_changed_manifest_before_mutation_refused(self):
        self.snapshot.side_effect = [
            (Path("/tmp/staged.plist"), "a" * 64),
            (Path("/tmp/staged.plist"), "a" * 64),
            (Path("/tmp/staged.plist"), "b" * 64),
            (Path("/tmp/staged.plist"), "b" * 64),
        ]
        self.plist.return_value = "/tmp/staged.plist"
        with self.assertRaisesRegex(FreshStopError, "TARGET_CHANGED"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_disappeared_pid_refused(self):
        self.pid_alive.side_effect = None
        self.pid_alive.return_value = False
        with self.assertRaisesRegex(FreshStopError, "PROCESS_CHANGED"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_pid_permission_unknown_refused(self):
        self.pid_alive.side_effect = None
        self.pid_alive.return_value = None
        with self.assertRaisesRegex(FreshStopError, "PROCESS_CHANGED"):
            self.shutdown()
        self.runner.assert_not_called()

    def test_launchctl_error_never_retries(self):
        self.runner.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(FreshStopError, "OUTCOME_UNKNOWN"):
            self.shutdown()
        self.runner.assert_called_once()

    def test_launchctl_timeout_never_retries(self):
        self.runner.side_effect = subprocess.TimeoutExpired("launchctl", 15)
        with self.assertRaisesRegex(FreshStopError, "OUTCOME_UNKNOWN"):
            self.shutdown()
        self.runner.assert_called_once()

    def test_post_unload_unknown_never_claims_stop(self):
        with self.assertRaisesRegex(FreshStopError, "OUTCOME_UNKNOWN"):
            self.shutdown()
        self.runner.assert_called_once()

    def test_post_unload_pid_still_alive_is_unknown(self):
        self.loaded.side_effect = [True] * 4 + [False]
        self.pid_alive.side_effect = [True, True]
        with self.assertRaisesRegex(FreshStopError, "OUTCOME_UNKNOWN"):
            self.shutdown()
        self.runner.assert_called_once()

    def test_secret_from_probe_never_leaks(self):
        self.live.side_effect = RuntimeError("pairing secret in error")
        with self.assertRaises(FreshStopError) as caught:
            self.shutdown()
        self.assertEqual(str(caught.exception), "FRESH_STOP_PREFLIGHT_REFUSED")
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
