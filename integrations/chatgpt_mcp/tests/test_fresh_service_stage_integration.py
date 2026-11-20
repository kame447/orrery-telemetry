"""The real pinned gateway release checker can prepare an inert plist."""
from pathlib import Path
import json
import sys

from orrery_chatgpt_mcp import service
from orrery_chatgpt_mcp.fresh_release_plan import Plan
from orrery_chatgpt_mcp.fresh_service_stage import preview, stage
from orrery_chatgpt_mcp.fresh_service_status import _match_plist, _read_plist


def test_real_release_integrity_and_inert_launchagent_staging(tmp_path):
    home = (tmp_path / 'operator-home').resolve()
    home.mkdir(mode=0o700)
    (home / 'Library').mkdir(mode=0o700)
    (home / 'Library' / 'LaunchAgents').mkdir(mode=0o700)
    releases = (tmp_path / 'releases').resolve()
    releases.mkdir(mode=0o700)
    source = Path(__file__).resolve().parents[1] / 'src'
    commit = 'a' * 40  # fixture metadata, not a provenance signature
    release_dir, release_digest = service.install_release(source, releases, source_commit=commit)
    assert service.verify_release(release_dir, release_digest)

    state = home / 'private-state'
    state.mkdir(mode=0o700)
    pairing = state / 'pairing.json'
    pairing.write_text(json.dumps({'secret':'fixture-inert-not-a-real-url'}))
    pairing.chmod(0o600)
    policy = state / 'policy.json'
    policy.write_text(json.dumps({
        'kind':'orrery-mcp-fresh-message-policy-v1',
        'context_path':str(state / 'clients' / 'prepared-client' / 'context.json'),
        'mail_instance_id':'1'*32,'sender_agent_id':31,
        'recipients':[{'target_agent_id':32,'expected_target_name':'TestRecipient','kind':'to'}],
    }))
    policy.chmod(0o600)
    plan = Plan(
        python=Path(sys.executable).resolve(), release_dir=release_dir,
        release_digest=release_digest, source_commit=commit, state_dir=state,
        policy_path=policy, label='org.agentstack.chatgpt-mcp-fresh.fixture',
        port=8792, mail_instance_id='1'*32, sender_agent_id=31,
        recipients=((32,'TestRecipient','to'),),
    )
    to_stage = preview(plan, home, verify_release=service.verify_release,
                       read_release=service.read_release)
    assert to_stage['operation'] == 'preview_only'
    calls=[]
    result = stage(plan, home, to_stage['approval_digest'],
                   verify_release=service.verify_release, read_release=service.read_release,
                   is_loaded=lambda label: calls.append(label) or False)
    assert result['operation'] == 'staged_inactive'
    assert calls == [plan.label]
    assert result['process_started'] is False
    target = home / 'Library' / 'LaunchAgents' / (plan.label+'.plist')
    _match_plist(plan, _read_plist(target))
    assert service.verify_release(release_dir, release_digest)
