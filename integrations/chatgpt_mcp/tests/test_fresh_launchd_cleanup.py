"""Failure-path teardown checks run in ordinary CI without macOS or launchd."""
from pathlib import Path
import subprocess
import sys
import traceback
import unittest
from unittest.mock import Mock, patch

from fresh_launchd_cleanup import cleanup_launchagent


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.target = 'gui/501/org.agentstack.chatgpt-mcp-fresh.fixture-test-0123456789abcdef'
        self.launchctl = Mock(side_effect=self.command)
        self.pid_exists = Mock(return_value=False)
        self.log = Mock(spec=Path)
        self.log.read_text.return_value = 'clean fixture log'
        self.client = Mock()
        self.client.connect_ex.return_value = 61
        sock = self.enterContext(patch('fresh_launchd_cleanup.socket.socket'))
        sock.return_value.__enter__.return_value = self.client

    @staticmethod
    def command(operation, target):
        return subprocess.CompletedProcess([operation, target], 0 if operation == 'bootout' else 1)

    def cleanup(self, *, attempted=True, body_error=None):
        cleanup_launchagent(
            target=self.target, attempted=attempted, observed_pids=(111, 222),
            launchctl=self.launchctl, pid_exists=self.pid_exists, port=54321,
            log_path=self.log, forbidden=('PRIVATE_SECRET',), body_error=body_error,
        )

    def assert_all_observed(self, *, bootout=True):
        operations = [call.args[0] for call in self.launchctl.call_args_list]
        self.assertEqual(operations.count('bootout'), int(bootout))
        self.assertEqual(operations.count('print'), 1)
        self.assertEqual({call.args for call in self.pid_exists.call_args_list}, {(111,), (222,)})
        self.client.connect_ex.assert_called_once_with(('127.0.0.1', 54321))
        self.log.read_text.assert_called_once_with(errors='replace')
        self.assertTrue(all(call.args[1] == self.target for call in self.launchctl.call_args_list))

    def test_success_verifies_every_observation(self):
        self.cleanup()
        self.assert_all_observed()

    def test_no_second_bootout_after_an_earlier_attempt(self):
        self.cleanup(attempted=False)
        self.assert_all_observed(bootout=False)

    def test_failed_bootout_does_not_skip_observations(self):
        self.launchctl.side_effect = lambda operation, target: subprocess.CompletedProcess(
            [operation, target], 1, 'PRIVATE_SECRET', 'PRIVATE_SECRET',
        )
        with self.assertRaisesRegex(AssertionError, 'BOOTOUT_UNVERIFIED'):
            self.cleanup()
        self.assert_all_observed()

    def test_timed_out_bootout_does_not_skip_observations_or_leak_output(self):
        def command(operation, target):
            if operation == 'bootout':
                raise subprocess.TimeoutExpired('PRIVATE_SECRET', 15, output='PRIVATE_SECRET')
            return self.command(operation, target)
        self.launchctl.side_effect = command
        try:
            self.cleanup()
        except AssertionError as error:
            rendered = ''.join(traceback.format_exception(error))
            self.assertIn('BOOTOUT_UNVERIFIED', rendered)
            self.assertNotIn('PRIVATE_SECRET', rendered)
        else:
            self.fail('unverified bootout was accepted')
        self.assert_all_observed()

    def test_independent_observation_failures_are_aggregated(self):
        def command(operation, target):
            raise OSError('PRIVATE_SECRET')
        self.launchctl.side_effect = command
        self.pid_exists.side_effect = lambda pid: None if pid == 111 else False
        self.client.connect_ex.return_value = 0
        self.log.read_text.return_value = 'PRIVATE_SECRET'
        with self.assertRaises(AssertionError) as caught:
            self.cleanup()
        summary = str(caught.exception)
        for code in ('BOOTOUT_UNVERIFIED', 'JOB_REMOVAL_UNVERIFIED', 'PID_EXIT_UNVERIFIED',
                     'PORT_CLOSE_UNVERIFIED', 'LOG_PRIVACY_UNVERIFIED'):
            self.assertIn(code, summary)
        self.assertNotIn('PRIVATE_SECRET', summary)
        self.assert_all_observed()

    def test_pid_probe_exception_does_not_skip_other_pids(self):
        def exists(pid):
            if pid == 111:
                raise OSError('PRIVATE_SECRET')
            return False
        self.pid_exists.side_effect = exists
        with self.assertRaisesRegex(AssertionError, 'PID_EXIT_UNVERIFIED'):
            self.cleanup()
        self.assert_all_observed()

    def test_body_failure_is_preserved_with_sanitized_cleanup_note(self):
        original = AssertionError('original test body failed')
        self.launchctl.side_effect = lambda operation, target: subprocess.CompletedProcess(
            [operation, target], 1,
        )
        try:
            try:
                raise original
            finally:
                self.cleanup(body_error=sys.exception())
        except AssertionError as error:
            self.assertIs(error, original)
            self.assertEqual(error.__notes__, ['isolated LaunchAgent cleanup: BOOTOUT_UNVERIFIED'])
        else:
            self.fail('the test body failure was lost')
        self.assert_all_observed()

    def test_successful_cleanup_does_not_replace_body_failure(self):
        original = AssertionError('original test body failed')
        try:
            try:
                raise original
            finally:
                self.cleanup(body_error=sys.exception())
        except AssertionError as error:
            self.assertIs(error, original)
            self.assertFalse(hasattr(error, '__notes__'))
        else:
            self.fail('the test body failure was lost')
        self.assert_all_observed()


if __name__ == '__main__':
    unittest.main()
