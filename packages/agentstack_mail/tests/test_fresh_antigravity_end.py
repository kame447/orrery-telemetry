"""Native process-group closure, with a real surviving group-member regression."""
from __future__ import annotations

import errno
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agentstack_mail import fresh_antigravity_end as native_end
from agentstack_mail import fresh_antigravity_runner as native_runner
from agentstack_mail import fresh_child
from agentstack_mail import fresh_end as end
from agentstack_mail.fresh_launch import identity
from agentstack_mail.fresh_runtime import _read_private, _write_private
from agentstack_mail.storage_origin_contract import B1Error
from test_fresh_end import ROOT, close, closed_candidate, replace


@pytest.fixture
def runner_cleanup(tmp_path, monkeypatch):
    runner = native_runner.Runner.__new__(native_runner.Runner)
    pid = os.getpid() + 100_000_000
    row = (os.getpid(), os.getuid(), 'native-birth', pid)
    state = {'rows': {pid: row}, 'status': 'S', 'waited': False, 'events': [], 'signals': []}
    runner.run = tmp_path
    runner.state = tmp_path / 'antigravity';runner.state.mkdir(mode=0o700)
    runner.expected = dict(mail_instance_id='fixture-instance', mail_agent_id=3, credential_generation=1)
    runner.plan = {'wrapper_root': str(ROOT)}
    runner.intent = {'child_spawn_id': 'fixture-spawn'}
    runner.owns_run = True
    runner.listener = runner.socket_path = None
    runner.native_code = None
    runner.native_record = dict(pid=pid, process_start='native-birth', pgid=pid)
    runner.processes = {'process_snapshot': lambda **_: state['rows']}

    def wait(*, timeout):
        assert state['rows'] == {pid: row} and state['status'] == 'Z'
        state['waited'] = True
        state['rows'] = {}
        state['events'].append('wait')
        return 0

    runner.process = SimpleNamespace(pid=pid, stdin=io.BytesIO(), stdout=io.BytesIO(), wait=wait)

    async def call(name, arguments):
        assert (runner.state / 'native-ended.json').is_file()
        state['events'].append((name, arguments))
        return {}

    runner.client = SimpleNamespace(context_digest='fixture-context', call=call)

    def verify(client, root, run, *, runner_pid):
        assert client is runner.client and root == ROOT and run == runner.run
        assert runner_pid == os.getpid() and state['waited'] and not state['rows']
        state['events'].append('verify')
        return {'process': {'state': 'absent'}, 'group': {'state': 'empty'}}

    def process_state(arguments, **_):
        assert arguments == ['ps', '-p', str(pid), '-o', 'stat=']
        return subprocess.CompletedProcess(arguments, 0, state['status'] + '\n', '')

    monkeypatch.setattr(native_end, 'verify_native_ended', verify)
    resources = [str(tmp_path / 'owned.txt')]
    monkeypatch.setattr(fresh_child, 'launch_task', lambda _: (
        {'spawn_id': 'fixture-spawn', 'resources': resources}, None))
    monkeypatch.setattr(native_runner.subprocess, 'run', process_state)
    monkeypatch.setattr(native_runner.os, 'killpg', lambda *_: pytest.fail('unexpected signal'))
    return runner, state, resources


@pytest.mark.parametrize('exit_before_signal', [True, False])
def test_runner_reaps_proven_exit_before_releasing_reservations(runner_cleanup, monkeypatch, exit_before_signal):
    runner, state, resources = runner_cleanup
    if exit_before_signal:
        state['status'] = 'Z'

    def signal_denied(pgid, signum):
        assert pgid == runner.process.pid
        state['signals'].append(signum)
        state['status'] = 'Z'
        raise PermissionError(errno.EPERM, 'fixture signal denial')

    monkeypatch.setattr(native_runner.os, 'killpg', signal_denied)
    runner.close()
    assert len(state['signals']) == (0 if exit_before_signal else 1)
    assert runner.native_code == 0
    assert state['events'] == ['wait', 'verify', ('release_reservations', {'paths': resources})]
    assert (runner.state / 'native-ended.json').is_file()


@pytest.mark.parametrize('boundary', ['live', 'group-member', 'identity-changed', 'unavailable'])
def test_runner_eperm_without_proven_exit_retains_reservations(runner_cleanup, monkeypatch, boundary):
    runner, state, _ = runner_cleanup
    pid = runner.process.pid

    def signal_denied(pgid, signum):
        assert pgid == pid
        state['signals'].append(signum)
        if boundary == 'group-member':
            state['status'] = 'Z'
            state['rows'][pid + 1] = (pid, os.getuid(), 'member-birth', pid)
        elif boundary == 'identity-changed':
            state['status'] = 'Z'
            state['rows'][pid] = (os.getpid(), os.getuid(), 'different-birth', pid)
        elif boundary == 'unavailable':
            state['rows'] = None
        raise PermissionError(errno.EPERM, 'fixture signal denial')

    monkeypatch.setattr(native_runner.os, 'killpg', signal_denied)
    with pytest.raises((PermissionError, B1Error)):
        runner.close()
    assert len(state['signals']) == 1 and not state['waited']
    assert state['events'] == []
    assert not (runner.state / 'native-ended.json').exists()


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
