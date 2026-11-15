"""Pure stdlib tests using private fixture files, never installed services."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from orrery_chatgpt_mcp.fresh_release_plan import Plan, PlanError, inspect


INSTANCE = "0" * 32
DIGEST = "a" * 64
COMMIT = "b" * 40


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        state = root / "state"
        state.mkdir(mode=0o700)
        (state / "pairing.json").write_text('{"secret":"fixture"}')
        (state / "pairing.json").chmod(0o600)
        policy = root / "policy.json"
        self.value = {"kind":"orrery-mcp-fresh-message-policy-v1",
                      "context_path": str(root / "client/context.json"),
                      "mail_instance_id": INSTANCE, "sender_agent_id": 5,
                      "recipients":[{"target_agent_id":7,"expected_target_name":"Target","kind":"to"}]}
        policy.write_text(json.dumps(self.value));policy.chmod(0o600)
        release = root / "release";release.mkdir()
        self.plan=Plan(Path(sys.executable).resolve(),release,DIGEST,COMMIT,state,policy,
                       "org.agentstack.chatgpt-mcp-fresh.test",8792,INSTANCE,5,((7,"Target","to"),))
        self.calls=[]

    def check(self, plan=None, *, ok=True, record=True):
        def verify(*args):
            self.calls.append(args)
            return ok
        return inspect(plan or self.plan,verify,
                       lambda _: {"digest":DIGEST,"source_commit":COMMIT} if record else {})

    def test_expected_immutable_read_only_preflight(self):
        value=self.check()
        self.assertTrue(value["validated"])
        self.assertFalse(value["live_process_verified"])
        self.assertFalse(value["dependencies_verified"])
        self.assertEqual(len(self.calls),1)
        self.assertNotIn(self.value["context_path"],json.dumps(value))
        self.assertNotIn("secret",json.dumps(value))

    def test_existing_service_label_and_port_refused(self):
        from dataclasses import replace
        for bad in ["org.agentstack.chatgpt-mcp","org.agentstack.mail"]:
            with self.assertRaises(PlanError):replace(self.plan,label=bad)
        for bad in [8790,8791,8770,18765,True,70000]:
            with self.assertRaises(PlanError):replace(self.plan,port=bad)

    def test_invalid_authority_refused(self):
        from dataclasses import replace
        for v in [True,0]:
            with self.assertRaises(PlanError):replace(self.plan,sender_agent_id=v)
        with self.assertRaises(PlanError):replace(self.plan,recipients=((7,"Target","to"),(7,"Target","to")))
        with self.assertRaises(PlanError):replace(self.plan,recipients=((7,"Target","cc"),))

    def test_policy_mismatch_no_release_check(self):
        self.value["recipients"][0]["expected_target_name"]="Other"
        self.plan.policy_path.write_text(json.dumps(self.value))
        with self.assertRaisesRegex(PlanError,"AUTHORITY_UNVERIFIED"):self.check()
        self.assertFalse(self.calls)

    def test_policy_world_readable_fails(self):
        self.plan.policy_path.chmod(0o644)
        with self.assertRaisesRegex(PlanError,"POLICY_UNVERIFIED"):self.check()
        self.assertFalse(self.calls)

    def test_policy_symlink_and_hardlink_fail(self):
        original=self.plan.policy_path.with_name("original.json")
        self.plan.policy_path.rename(original)
        self.plan.policy_path.symlink_to(original)
        with self.assertRaisesRegex(PlanError,"POLICY_UNVERIFIED"):self.check()
        self.plan.policy_path.unlink()
        os.link(original,self.plan.policy_path)
        with self.assertRaisesRegex(PlanError,"POLICY_UNVERIFIED"):self.check()

    def test_duplicate_json_field_refused(self):
        self.plan.policy_path.write_text('{"kind":1,"kind":2}')
        with self.assertRaisesRegex(PlanError,"POLICY_UNVERIFIED"):self.check()

    def test_pairing_private_and_state_dir_required(self):
        (self.plan.state_dir/"pairing.json").chmod(0o644)
        with self.assertRaisesRegex(PlanError,"POLICY_UNVERIFIED"):self.check()
        (self.plan.state_dir/"pairing.json").chmod(0o600)
        self.plan.state_dir.chmod(0o755)
        with self.assertRaisesRegex(PlanError,"STATE_UNVERIFIED"):self.check()

    def test_release_pin_invalid(self):
        from dataclasses import replace
        with self.assertRaisesRegex(PlanError,"RELEASE_PIN_INVALID"):replace(self.plan,source_commit="x")
        with self.assertRaisesRegex(PlanError,"RELEASE_UNVERIFIED"):self.check(ok=False)
        with self.assertRaisesRegex(PlanError,"RELEASE_UNVERIFIED"):self.check(record=False)

    def test_context_path_changed_refuses(self):
        self.value["context_path"]="relative/evil"
        self.plan.policy_path.write_text(json.dumps(self.value))
        with self.assertRaisesRegex(PlanError,"AUTHORITY_UNVERIFIED"):self.check()


if __name__ == "__main__":
    unittest.main()
