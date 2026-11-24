"""Native process-group closure, with a real surviving group-member regression."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from agentstack_mail import fresh_antigravity_end as native_end
from agentstack_mail import fresh_end as end
from agentstack_mail.fresh_launch import identity
from agentstack_mail.fresh_runtime import _read_private, _write_private
from agentstack_mail.storage_origin_contract import B1Error
from test_fresh_end import ROOT, close, closed_candidate, replace


def native_record(fixture, *, pid=201, start='native-birth'):
    client, run, intent, route, *_ = fixture
    intent['provider'] = 'antigravity';replace(run / 'intent.json', intent)
    state = run / 'antigravity';state.mkdir(mode=0o700)
    value = dict(pid=pid, process_start=start, pgid=pid, runner_pid=route['provider']['pid'],
                 launch_id=run.name, context_digest=client.context_digest, **identity(client))
    _write_private(state / 'native-process.json', value)
    return value


@pytest.mark.parametrize('damage', ['native-alive', 'pid-reused', 'group-child', 'wrong-owner', 'both-records'])
def test_native_ambiguity_keeps_normal_and_operator_closure_unknown(closed_candidate, monkeypatch, damage):
    client, run, intent, route, state, api, _ = closed_candidate
    proof = native_record(closed_candidate)
    monkeypatch.setattr(native_end, 'process_api', lambda _: api)
    if damage == 'native-alive':
        state['rows'][201] = (102, os.getuid(), 'native-birth', 201)
        state['starts'][201] = 'native-birth'
    elif damage == 'pid-reused':
        state['rows'][201] = (1, os.getuid(), 'replacement', 201)
        state['starts'][201] = 'replacement'
    elif damage == 'group-child':
        state['rows'][202] = (1, os.getuid(), 'surviving-child', 201)
    elif damage == 'wrong-owner':
        proof['mail_agent_id'] += 1
        replace(run / 'antigravity/native-process.json', proof)
    else:
        _write_private(run / 'antigravity/native-not-started.json', dict(
            launch_id=run.name, context_digest=client.context_digest, **identity(client)))
    before = {p: p.read_bytes() for p in run.rglob('*.json')}
    with pytest.raises(B1Error):native_end.verify_native_ended(client, ROOT, run)
    with pytest.raises(B1Error):close(closed_candidate)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert not (run / 'ended.json').exists() and not (run / 'resolved-ended.json').exists()


def test_operator_resolution_retains_proof_after_wrapper_removal_and_pid_reuse(closed_candidate, monkeypatch, tmp_path):
    client, run, intent, route, state, api, _ = closed_candidate
    wrapper = tmp_path / 'temporary-wrapper'
    (wrapper / 'bin').mkdir(parents=True)
    executable = wrapper / 'bin/agentstack-runtime-client';executable.touch()
    intent['wrapper_root'] = str(wrapper);replace(run / 'intent.json', intent)
    native_record(closed_candidate)
    monkeypatch.setattr(native_end, 'process_api', lambda _: api)
    assert end.resolve_ended(client, wrapper, run.name, confirm_ended=True)['end_kind'] == 'resolved'
    value = _read_private(run / 'resolved-ended.json')
    assert value['native_observation']['process']['state'] == 'absent'
    assert value['native_observation']['group'] == {'pgid': 201, 'state': 'empty'}
    # Historic proof is not a new termination claim about a subsequently reused PID.
    state['rows'][201] = (1, os.getuid(), 'later-unrelated-process', 201)
    state['starts'][201] = 'later-unrelated-process'
    executable.unlink();(wrapper / 'bin').rmdir();wrapper.rmdir()
    assert end.read_run_end(client, run).kind == 'resolved'
    value['native_observation']['record_digest'] = 'changed'
    replace(run / 'resolved-ended.json', value)
    with pytest.raises(B1Error, match='FRESH_LAUNCH_OUTCOME_UNKNOWN'):
        end.read_run_end(client, run)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group evidence')
def test_real_group_member_outlives_native_leader_and_blocks_end(closed_candidate):
    client, run, intent, route, *_ = closed_candidate
    leader = member = None
    try:
        # Both are direct test children so they can always be reaped. Their
        # separate group has the same leader/member property as native agy.
        leader = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
            process_group=0, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        member = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
            process_group=leader.pid, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        api = native_end.process_api(ROOT)
        start = api['process_start'](leader.pid)
        assert start not in (None, 'absent')
        native_record(closed_candidate, pid=leader.pid, start=start)
        leader.terminate();leader.wait(timeout=5)
        with pytest.raises(B1Error, match='FRESH_ANTIGRAVITY_GROUP_ALIVE'):
            native_end.verify_native_ended(client, ROOT, run)
        assert member.poll() is None
        member.terminate();member.wait(timeout=5)
        observation = native_end.verify_native_ended(client, ROOT, run)
        assert observation['process']['state'] == 'absent' and observation['group']['state'] == 'empty'
    finally:
        for process in (member, leader):
            if process is not None and process.poll() is None:
                process.kill();process.wait(timeout=5)
