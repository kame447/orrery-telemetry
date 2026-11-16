"""No launchctl or real service effects: operator-only offline plist staging."""
from dataclasses import replace
import os
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from orrery_chatgpt_mcp.fresh_release_plan import Plan
from orrery_chatgpt_mcp.fresh_service_stage import StageError, preview, stage
from orrery_chatgpt_mcp.fresh_service_status import _match_plist, _read_plist


class StageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.library = self.home / 'Library'
        self.library.mkdir(mode=0o700)
        self.agents = self.library / 'LaunchAgents'
        self.agents.mkdir(mode=0o700)
        (self.home / 'state').mkdir(mode=0o700)
        policy = self.home / 'policy.json'
        self.policy_data = {
            'kind':'orrery-mcp-fresh-message-policy-v1',
            'context_path':str(self.home/'clients'/'prepared'/'context.json'),
            'mail_instance_id':'1'*32, 'sender_agent_id':7,
            'recipients':[{'target_agent_id':8,'expected_target_name':'AgentTwo','kind':'to'}],
        }
        policy.write_text(json.dumps(self.policy_data));policy.chmod(0o600)
        self.plan = Plan(
            python=self.home/'venv/bin/python', release_dir=self.home/'release',
            release_digest='a'*64, source_commit='b'*40,
            state_dir=self.home/'state', policy_path=self.home/'policy.json',
            label='org.agentstack.chatgpt-mcp-fresh.fixture', port=8792,
            mail_instance_id='1'*32, sender_agent_id=7, recipients=((8,'AgentTwo','to'),),
        )
        self.verify=Mock(return_value=True)
        self.release=Mock(return_value={'digest':'a'*64,'source_commit':'b'*40})
        self.loaded=Mock(return_value=False)
        self.inspector=patch('orrery_chatgpt_mcp.fresh_service_stage.inspect',
            side_effect=lambda plan, verify, read: {'validated': verify(plan.release_dir,plan.release_digest,str(plan.python)) and read(plan.release_dir)['digest']==plan.release_digest})
        self.inspector.start();self.addCleanup(self.inspector.stop)

    def show(self, plan=None, home=None):
        return preview(plan or self.plan, home or self.home, verify_release=self.verify, read_release=self.release)

    def apply(self, digest=None, plan=None, home=None):
        if digest is None: digest=self.show(plan,home)['approval_digest']
        return stage(plan or self.plan, home or self.home, digest, verify_release=self.verify,
                     read_release=self.release, is_loaded=self.loaded)

    def target(self):
        return self.agents / (self.plan.label+'.plist')

    def test_preview_does_not_write_or_launch(self):
        result=self.show()
        self.assertEqual(result,self.show())
        self.assertFalse(result['process_started'])
        self.assertFalse(result['dependencies_verified'])
        self.assertFalse(self.target().exists())
        self.loaded.assert_not_called()
        self.assertNotIn('policy.json',str(result))
        self.assertNotIn(str(self.home),str(result))

    def test_stage_exact_manifest_without_process_change(self):
        receipt=self.apply()
        self.assertEqual(receipt['operation'],'staged_inactive')
        self.assertFalse(receipt['process_started'])
        self.assertEqual(self.loaded.call_count,1)
        file=self.target()
        self.assertEqual(file.stat().st_mode & 0o777,0o644)
        _match_plist(self.plan,_read_plist(file))
        self.assertEqual(plistlib.loads(file.read_bytes())['Label'],self.plan.label)
        self.assertEqual(set(self.agents.iterdir()),{file})

    def test_isolated_dashboard_endpoint_changes_plist_and_approval(self):
        old = self.show()["approval_digest"]
        alternate = replace(self.plan, dashboard_url="http://127.0.0.1:43219")
        changed = self.show(alternate)
        self.assertNotEqual(changed["approval_digest"], old)
        with self.assertRaisesRegex(StageError, "APPROVAL_MISMATCH"):
            self.apply(old, alternate)
        self.apply(changed["approval_digest"], alternate)
        spec = _read_plist(self.target())
        self.assertEqual(
            spec["EnvironmentVariables"]["ORRERY_MCP_DASHBOARD_URL"],
            alternate.dashboard_url,
        )
        _match_plist(alternate, spec)

    def test_mismatched_digest_refuses_before_launchd_or_write(self):
        with self.assertRaisesRegex(StageError,'APPROVAL_MISMATCH'):
            self.apply('f'*64)
        self.loaded.assert_not_called()
        self.assertFalse(self.target().exists())

    def test_changed_release_or_authority_invalidates_approval(self):
        old=self.show()['approval_digest']
        for new_plan in [replace(self.plan,source_commit='f'*40),
                         replace(self.plan,recipients=((9,'AgentThree','to'),)),
                         replace(self.plan,release_digest='0'*64)]:
            with self.subTest(new_plan=new_plan):
                # Static policy or release verification may refuse before approval comparison.
                with self.assertRaisesRegex(StageError,'APPROVAL_MISMATCH|STATIC_PLAN_UNVERIFIED'):
                    self.apply(old,new_plan)
        self.assertFalse(self.target().exists())

    def test_context_pointer_change_invalidates_original_approval(self):
        approved=self.show()['approval_digest']
        self.policy_data['context_path']=str(self.home/'other-client'/'context.json')
        self.plan.policy_path.write_text(json.dumps(self.policy_data))
        with self.assertRaisesRegex(StageError,'APPROVAL_MISMATCH'):
            self.apply(approved)
        self.assertFalse(self.target().exists())

    def test_existing_file_never_overwritten(self):
        dest=self.target();dest.write_bytes(b'valuable-unrelated-configuration')
        before=dest.read_bytes()
        with self.assertRaisesRegex(StageError,'ALREADY_EXISTS'):self.apply()
        self.assertEqual(dest.read_bytes(),before)

    def test_existing_symlink_is_not_overwritten(self):
        outside=self.home/'outside.txt';outside.write_text('keep')
        self.target().symlink_to(outside)
        with self.assertRaisesRegex(StageError,'ALREADY_EXISTS'):self.apply()
        self.assertEqual(outside.read_text(),'keep')

    def test_preexisting_loaded_or_unknown_state_refused(self):
        for answer in [True,None,1]:
            with self.subTest(answer=answer):
                self.loaded.return_value=answer
                with self.assertRaisesRegex(StageError,'LOADED_OR_UNKNOWN'):self.apply()
                self.assertFalse(self.target().exists())

    def test_launchctl_read_failure_is_not_treated_as_absent(self):
        self.loaded.side_effect=TimeoutError('private service information')
        with self.assertRaisesRegex(StageError,'STAGE_FAILED') as error:self.apply()
        self.assertNotIn('private service information',str(error.exception))
        self.assertFalse(self.target().exists())

    def test_racing_file_creator_wins_without_overwrite(self):
        real=os.link
        def collision(src,dst,*,src_dir_fd=None,dst_dir_fd=None,follow_symlinks=True):
            Path(dst).write_bytes(b'unrelated bytes')
            raise FileExistsError
        with patch('orrery_chatgpt_mcp.fresh_service_stage.os.link',side_effect=collision):
            with self.assertRaisesRegex(StageError,'STAGE_FAILED'):self.apply()
        self.assertEqual(self.target().read_bytes(),b'unrelated bytes')
        self.assertEqual(len(list(self.agents.iterdir())),1)

    def test_world_writable_or_wrong_owner_launch_directory_refused(self):
        self.agents.chmod(0o777)
        with self.assertRaisesRegex(StageError,'DIRECTORY_UNVERIFIED'):self.show()
        self.loaded.assert_not_called()

    def test_symlinked_launch_directory_refused(self):
        original=self.agents.with_name('real-LaunchAgents')
        self.agents.rename(original);self.agents.symlink_to(original)
        with self.assertRaisesRegex(StageError,'DIRECTORY_UNVERIFIED'):self.show()

    def test_wrong_home_path_refused(self):
        with self.assertRaisesRegex(StageError,'DIRECTORY_UNVERIFIED'):
            self.show(home=Path('/nonexistent-home-do-not-create'))

    def test_invalid_preflight_blocks_staging(self):
        self.verify.return_value=False
        with self.assertRaisesRegex(StageError,'STATIC_PLAN_UNVERIFIED'):self.apply('f'*64)
        self.loaded.assert_not_called()
        self.assertFalse(self.target().exists())


if __name__=='__main__':unittest.main()
