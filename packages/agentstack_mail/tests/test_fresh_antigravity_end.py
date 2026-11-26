"""Native process-group closure, with a real surviving group-member regression."""
from __future__ import annotations

import errno
import io
import os
from pathlib import Path
import subprocess
import runpy
import sys
from types import SimpleNamespace

import pytest

from agentstack_mail import fresh_antigravity_end as native_end
from agentstack_mail import fresh_antigravity_runner as native_runner
from agentstack_mail.fresh_antigravity_supervisor import NativeOwner
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
    runner.resume_history_admitted = True
    runner.listener = runner.socket_path = None
    runner.native_code = None
    owner = NativeOwner.__new__(NativeOwner)
    owner.native_record = dict(pid=pid, process_start='native-birth', pgid=pid)
    owner.processes = {'process_snapshot': lambda **_: state['rows']}
    owner.native_code = None

    def wait(*, timeout):
        assert state['rows'] == {pid: row} and state['status'] == 'Z'
        state['waited'] = True
        state['rows'] = {}
        state['events'].append('wait')
        return 0

    owner.process = SimpleNamespace(pid=pid, wait=wait)

    def stop():
        owner.stop_native()
        return owner.native_code

    runner.process = SimpleNamespace(pid=pid, stdin=io.BytesIO(), stdout=io.BytesIO(), stop=stop)

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
        assert arguments == ['/bin/ps', '-p', str(pid), '-o', 'stat=']
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


def test_admitted_resume_without_native_start_retains_reservations(runner_cleanup, monkeypatch):
    runner, state, _ = runner_cleanup
    runner.plan['resume_session_id'] = 'previous-session'
    runner.resume_history_admitted = True
    runner.process = None
    monkeypatch.setattr(native_end, 'verify_native_ended', lambda *args, **kwargs: {
        'kind': native_end.KIND, 'state': 'not-started', 'record_digest': 'fixture'})
    runner.close()
    assert (runner.state / 'native-not-started.json').is_file()
    assert (runner.state / 'native-ended.json').is_file()
    assert state['events'] == []


def test_missing_child_task_is_not_reported_as_success(runner_cleanup, monkeypatch):
    runner, state, _ = runner_cleanup
    runner.resume_history_admitted = True
    runner.process = None
    monkeypatch.setattr(native_end, 'verify_native_ended', lambda *args, **kwargs: {
        'kind': native_end.KIND, 'state': 'not-started', 'record_digest': 'fixture'})
    monkeypatch.setattr(fresh_child, 'launch_task', lambda _: (None, None))
    with pytest.raises(B1Error, match='FRESH_CHILD_BINDING_MISMATCH'):
        runner.close()
    assert state['events'] == []


def test_new_nonstarted_child_retains_existing_release_contract(runner_cleanup, monkeypatch):
    runner, state, resources = runner_cleanup
    runner.resume_history_admitted = True
    runner.process = None
    monkeypatch.setattr(native_end, 'verify_native_ended', lambda *args, **kwargs: {
        'kind': native_end.KIND, 'state': 'not-started', 'record_digest': 'fixture'})
    runner.close()
    assert state['events'] == [('release_reservations', {'paths': resources})]


def test_nonchild_without_task_closes_normally(runner_cleanup, monkeypatch):
    runner, state, _ = runner_cleanup
    runner.resume_history_admitted = True
    runner.intent['child_spawn_id'] = None
    runner.process = None
    monkeypatch.setattr(native_end, 'verify_native_ended', lambda *args, **kwargs: {
        'kind': native_end.KIND, 'state': 'not-started', 'record_digest': 'fixture'})
    monkeypatch.setattr(fresh_child, 'launch_task', lambda _: (None, None))
    runner.close()
    assert state['events'] == []


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


def test_native_owner_cleans_direct_child_when_record_publication_fails(tmp_path):
    owner = NativeOwner.__new__(NativeOwner)
    owner.run = tmp_path
    owner.state = tmp_path / 'antigravity';owner.state.mkdir(mode=0o700)
    owner.expected = {}
    owner.runner_pid = os.getpid()
    owner.client = SimpleNamespace(context_digest='fixture-context')
    owner.native_record = owner.native_code = None
    owner.processes = runpy.run_path(str(ROOT / 'bin/lib/process_observation.py'))
    occupied = owner.state / 'native-process.json'
    occupied.write_bytes(b'untouched')
    owner.process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'],
        start_new_session=True)
    try:
        with pytest.raises(FileExistsError):
            owner.capture_native()
        assert owner.native_record['pid'] == owner.process.pid
        owner.stop_native()
        assert owner.native_code is not None
        assert occupied.read_bytes() == b'untouched'
        assert owner.processes['process_start'](owner.process.pid) == 'absent'
    finally:
        # A direct, unreaped fixture child is owned by this test.
        if owner.process.returncode is None:
            owner.process.kill()
            owner.process.wait(timeout=5)


@pytest.mark.parametrize('release', [False, True])
def test_native_gate_requires_explicit_release_before_plan_or_exec(monkeypatch, release):
    from agentstack_mail import fresh_antigravity_supervisor as supervisor

    calls = []
    argv = ['/fixture/native', '--fixture']
    def plan(path):
        calls.append(('plan', path))
        return None, {'argv': argv}, None, None
    def execute(binary, args):
        calls.append(('exec', binary, args))
    monkeypatch.setattr(supervisor, 'read_plan', plan)
    monkeypatch.setattr(supervisor.os, 'execv', execute)
    read, write = os.pipe()
    if release:
        os.write(write, b'1')
    os.close(write)
    if release:
        with pytest.raises(B1Error, match='FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED'):
            supervisor.gated_native('/fixture/plan.json', read)
        assert calls == [('plan', Path('/fixture/plan.json'))]
    else:
        assert supervisor.gated_native('/fixture/plan.json', read) == 1 and calls == []
    with pytest.raises(OSError):
        os.fstat(read)

def test_supervisor_keeps_ownership_until_cleanup_is_proven():
    from agentstack_mail.fresh_antigravity_supervisor import finish_released_native
    calls = []
    def stop():
        calls.append('observe')
        if calls.count('observe') < 3:
            raise B1Error('FRESH_ANTIGRAVITY_PROCESS_UNCONFIRMED')
    finish_released_native(SimpleNamespace(stop_native=stop),
        sleep=lambda seconds: calls.append(seconds))
    assert calls == ['observe', 1, 'observe', 2, 'observe']


def test_supervisor_timeout_does_not_kill_ownership_anchor():
    from agentstack_mail.fresh_antigravity_supervisor import SupervisedNative
    import socket
    calls = []
    def wait(*, timeout):
        calls.append(timeout)
        raise subprocess.TimeoutExpired('fixture supervisor', timeout)
    process = SupervisedNative()
    process.control, peer = socket.socketpair()
    process.worker = SimpleNamespace(wait=wait)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.stop()
        assert calls == [40]
        assert peer.recv(1) == b''
    finally:
        peer.close()


def test_owner_python_safe_path_blocks_workspace_package_shadow(tmp_path):
    from agentstack_mail import fresh_antigravity_supervisor as supervisor
    shadow = tmp_path / "agentstack_mail"
    shadow.mkdir()
    marker = tmp_path / "shadow-executed"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path; Path({str(marker)!r}).touch()")
    (shadow / "fresh_antigravity_supervisor.py").write_text("raise SystemExit(0)")
    read, write = os.pipe()
    os.close(write)
    try:
        env = dict(os.environ, PYTHONPATH=str(ROOT / "packages/agentstack_mail/src"))
        argv = supervisor._owner_argv(tmp_path / "not-read.json",
                                      "--gate-fd", str(read), "--watch-fd", str(read))
        proc = subprocess.run(argv, cwd=tmp_path, env=env, pass_fds=(read,),
                              capture_output=True, timeout=10)
        assert proc.returncode == 1 and not marker.exists(), proc.stderr
    finally:
        os.close(read)


def test_cleanup_retry_survives_diagnostic_failure(monkeypatch):
    from agentstack_mail import fresh_antigravity_supervisor as supervisor
    class BrokenStderr:
        def write(self, data):
            raise OSError("closed tty")
    calls = []
    def stop():
        calls.append(1)
        if len(calls) == 1:
            raise TypeError("bad observation")
    with monkeypatch.context() as scoped:
        scoped.setattr(supervisor.sys, "stderr", BrokenStderr())
        supervisor.finish_released_native(SimpleNamespace(stop_native=stop), sleep=lambda _: None)
    assert calls == [1, 1]


def test_runner_refuses_input_after_supervisor_control_loss():
    import socket
    runner = native_runner.Runner.__new__(native_runner.Runner)
    left, right = socket.socketpair()
    with left, right:
        stream = io.BytesIO()
        runner.process = SimpleNamespace(control=left, stdin=stream)
        runner.check = lambda: None
        runner.send("before")
        before = stream.getvalue()
        right.close()
        with pytest.raises(B1Error, match="FRESH_ANTIGRAVITY_SUPERVISOR_LOST"):
            runner.send("after")
        assert stream.getvalue() == before
