"""Contract tests for read-only live service status, using no macOS services."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp.fresh_service_status import StatusError, check_running


@dataclass(frozen=True)
class FakePlan:
    release_dir: Path
    release_digest: str
    source_commit: str
    python: Path
    state_dir: Path
    policy_path: Path
    label: str
    port: int
    mail_instance_id: str
    sender_agent_id: int
    recipients: tuple


class ServiceStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS tempfile may return /var while the trusted path contract requires /private/var.
        self.home = Path(self.temp.name).resolve()
        self.state = self.home / 'private'
        self.state.mkdir(mode=0o700)
        self.plan = FakePlan(
            release_dir=self.home/'release', release_digest='a'*64, source_commit='b'*40,
            python=self.home/'python', state_dir=self.state,
            policy_path=self.state/'policy.json', label='org.agentstack.chatgpt-mcp-fresh.test',
            port=8792, mail_instance_id='1'*32, sender_agent_id=19,
            recipients=((20,'AgentOne','to'),)
        )
        self.plist = self.home/'service.plist'
        self.spec = {
            'Label':self.plan.label,
            'ProgramArguments':[str(self.plan.python),'-m','orrery_chatgpt_mcp.fresh_server','serve'],
            'RunAtLoad':True, 'KeepAlive':True, 'Umask':0o077,
            'EnvironmentVariables':{
                'PYTHONPATH':str(self.plan.release_dir), 'ORRERY_MCP_MODE':'fresh',
                'ORRERY_MCP_AUTH':'pairing', 'ORRERY_MCP_ENABLE_MESSAGE':'1',
                'ORRERY_MCP_ENABLE_SPAWN':'0', 'ORRERY_MCP_PORT':'8792',
                'ORRERY_MCP_STATE_DIR':str(self.state),
                'ORRERY_MCP_FRESH_MESSAGE_POLICY':str(self.plan.policy_path),
                'ORRERY_MCP_DASHBOARD_URL':'http://127.0.0.1:8770',
            },
        }
        self._write()
        static=patch('orrery_chatgpt_mcp.fresh_release_plan.inspect',
                     side_effect=lambda plan, verify, read: {
                         'validated': verify(plan.release_dir, plan.release_digest, str(plan.python))
                         and read(plan.release_dir).get('digest') == plan.release_digest})
        static.start()
        self.addCleanup(static.stop)
        self.verify=Mock(return_value=True)
        self.release=Mock(return_value={'digest':'a'*64})
        self.pair=Mock(return_value='X'*43)
        self.live=Mock(return_value={
            'verified':True,'pid':333,'runtime_mode':'fresh','mail_instance_id':'1'*32,
            'sender_agent_id':19, 'recipient_count':1,
            'message_enabled_on_server':True,'spawn_enabled_on_server':False,
            'tools_verified':6,
        })

    def _write(self):
        self.plist.write_bytes(plistlib.dumps(self.spec))
        self.plist.chmod(0o644)

    def run_check(self):
        return check_running(self.plan, self.plist, verify_release=self.verify,
                             read_release=self.release, read_secret=self.pair, probe=self.live)

    def test_valid_attestation_keeps_unknown_as_unknown(self):
        result=self.run_check()
        self.assertTrue(result['verified'])
        self.assertEqual(result['pid'],333)
        self.assertTrue(result['effective_authority_verified'])
        self.assertFalse(result['fresh_client_delivery_verified'])
        self.assertFalse(result['process_import_provenance_verified'])
        self.assertNotIn('pairing_secret',result)
        self.assertNotIn('X'*43,repr(result))
        self.assertEqual(self.live.call_args.kwargs['pairing_secret'],'X'*43)
        self.assertEqual(self.live.call_args.kwargs['expected'].recipients, self.plan.recipients)

    def test_read_only_does_not_modify_plist(self):
        before=self.plist.read_bytes()
        self.run_check()
        self.assertEqual(self.plist.read_bytes(),before)

    def test_wrong_executable_refused_before_secret(self):
        self.spec['ProgramArguments'][0]='/other/python'
        self._write()
        with self.assertRaisesRegex(StatusError,'LAUNCH_CONFIGURATION_MISMATCH'):self.run_check()
        self.pair.assert_not_called();self.live.assert_not_called()

    def test_missing_or_extra_environment_refused(self):
        for update in (('ORRERY_MCP_ENABLE_MESSAGE','0'),('ORRERY_MCP_PROJECT_KEY','bad')):
            with self.subTest(update=update):
                self.spec['EnvironmentVariables'][update[0]]=update[1]
                self._write()
                with self.assertRaisesRegex(StatusError,'LAUNCH_CONFIGURATION_MISMATCH'):self.run_check()
                self.pair.assert_not_called()
                self.spec['EnvironmentVariables'].pop(update[0],None)
                if update[0]=='ORRERY_MCP_ENABLE_MESSAGE':self.spec['EnvironmentVariables'][update[0]]='1'

    def test_unknown_launchd_keys_are_refused(self):
        self.spec['Sockets']={'control':{'SockServiceName':'8792'}}
        self._write()
        with self.assertRaisesRegex(StatusError,'LAUNCH_CONFIGURATION_MISMATCH'):self.run_check()
        self.pair.assert_not_called()

    def test_wrong_mode_port_and_spawn_refused(self):
        for key,value in [('ORRERY_MCP_MODE','legacy'),('ORRERY_MCP_PORT','8790'),('ORRERY_MCP_ENABLE_SPAWN','1')]:
            with self.subTest(key=key):
                original=self.spec['EnvironmentVariables'][key]
                self.spec['EnvironmentVariables'][key]=value;self._write()
                with self.assertRaises(StatusError):self.run_check()
                self.spec['EnvironmentVariables'][key]=original

    def test_no_keepalive_or_invalid_umask_refused(self):
        for key,value in [('KeepAlive',False),('Umask',0o022),('RunAtLoad',False)]:
            with self.subTest(key=key):
                old=self.spec[key];self.spec[key]=value;self._write()
                with self.assertRaises(StatusError):self.run_check()
                self.spec[key]=old

    def test_symlink_and_hardlink_refused(self):
        original=self.home/'real.plist';self.plist.rename(original)
        self.plist.symlink_to(original)
        with self.assertRaisesRegex(StatusError,'PLIST_UNVERIFIED'):self.run_check()
        self.plist.unlink();os.link(original,self.plist)
        with self.assertRaisesRegex(StatusError,'PLIST_UNVERIFIED'):self.run_check()
        self.pair.assert_not_called()

    def test_unsafe_permissions_refused(self):
        self.plist.chmod(0o666)
        with self.assertRaisesRegex(StatusError,'PLIST_UNVERIFIED'):self.run_check()
        self.pair.assert_not_called()

    def test_missing_or_malformed_plist_refused(self):
        self.plist.unlink()
        with self.assertRaises(StatusError):self.run_check()
        self.plist.write_bytes(b'not a plist')
        with self.assertRaises(StatusError):self.run_check()

    def test_failed_static_release_blocks_pairing_secret(self):
        self.verify.return_value=False
        with self.assertRaisesRegex(StatusError,'RELEASE_UNVERIFIED'):self.run_check()
        self.pair.assert_not_called();self.live.assert_not_called()

    def test_probe_failure_is_fail_closed(self):
        self.live.side_effect=TimeoutError('PRIVATE_SECRET_IN_EXCEPTION')
        with self.assertRaisesRegex(StatusError,'FRESH_SERVICE_STATUS_UNVERIFIED') as result:self.run_check()
        self.assertNotIn('PRIVATE_SECRET',str(result.exception))

    def test_spoofed_success_fields_fail_closed(self):
        for field,value in [('pid',True),('sender_agent_id',True),('mail_instance_id','f'*32),
                            ('recipient_count',2),('tools_verified',5),('spawn_enabled_on_server',True),
                            ('message_enabled_on_server',False),('runtime_mode','legacy')]:
            with self.subTest(field=field):
                old=self.live.return_value[field]
                self.live.return_value[field]=value
                with self.assertRaisesRegex(StatusError,'AUTHORITY_UNVERIFIED'):self.run_check()
                self.live.return_value[field]=old

    def test_secret_not_exposed_on_wrong_length(self):
        self.pair.return_value='bad'
        with self.assertRaisesRegex(StatusError,'PAIRING_UNVERIFIED'):self.run_check()
        self.live.assert_not_called()

    def test_invalid_pairing_charset_is_refused(self):
        self.pair.return_value='!'*43
        with self.assertRaisesRegex(StatusError,'PAIRING_UNVERIFIED'):self.run_check()
        self.live.assert_not_called()

    def test_live_proof_uses_exact_expected_label_and_plist(self):
        self.run_check()
        values=self.live.call_args.kwargs
        self.assertEqual(values['label'],self.plan.label)
        self.assertEqual(values['plist_path'],str(self.plist))
        self.assertEqual(values['port'],8792)

if __name__=='__main__':unittest.main()
