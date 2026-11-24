"""Native-shaped fixture over real fresh hooks, proxy, stream and private socket.

No real provider account or tmux is used here; platform acceptance is separate.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import time
from uuid import uuid4

import pytest

from agentstack_mail.fresh_antigravity import _exchange
from agentstack_mail.fresh_child import family, handoff, prepare, status
from agentstack_mail.fresh_client import FreshBoundClient
from agentstack_mail.fresh_runtime import _read_private
from agentstack_mail.storage_origin_contract import B1Error
from test_fresh_antigravity_core import SyntheticTransport
from test_fresh_guard import grant, live
from test_fresh_notifications import rows
from test_fresh_proxy import environment

ROOT = Path(__file__).resolve().parents[3]

NATIVE = r'''
import json, os, pathlib, subprocess, sys, time, uuid

args = sys.argv[1:]
assert args[args.index('--input-format') + 1] == 'stream-json'
assert args[args.index('--output-format') + 1] == 'stream-json'
assert not {'--mode', '--dangerously-skip-permissions'} & set(args)
integration = pathlib.Path(args[args.index('--add-dir') + 1])
groups = json.loads((integration / '.agents/hooks.json').read_text())
hooks = next(iter(groups.values()))
mcp = next(iter(json.loads((integration / '.agents/mcp_config.json').read_text())['mcpServers'].values()))
session = args[args.index('--conversation') + 1] if '--conversation' in args else str(uuid.uuid4())
trace = pathlib.Path(os.environ['AGY_TEST_TRACE'])
mode = os.environ.get('AGY_TEST_MODE', 'normal')
state = pathlib.Path(os.environ['AGENTSTACK_FRESH_CLIENT_CONTEXT']).parent / 'launch/runs' / os.environ['AGENTSTACK_FRESH_LAUNCH_ID']

def record(kind, **values):
    with trace.open('a') as output:
        output.write(json.dumps(dict(kind=kind, monotonic=time.monotonic(), **values)) + '\n')

def emit(kind, **values):
    print(json.dumps(dict(event=kind, **values)), flush=True)

def hook(event, extra=None, env_changes=None, *, phase=None):
    handler = hooks[event][0]
    if 'hooks' in handler:
        handler = handler['hooks'][0]
    payload = dict(conversationId=session, workspacePaths=[os.getcwd(), str(integration)])
    payload.update(extra or {})
    record('hook-start', event=event, phase=phase or event)
    result = subprocess.run(['/bin/bash', '-c', handler['command']], input=json.dumps(payload),
        cwd=os.getcwd(), env=dict(os.environ, **(env_changes or {})), capture_output=True,
        text=True, timeout=30)
    record('hook-exit', event=event, phase=phase or event, returncode=result.returncode)
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    record('hook-result', event=event, phase=phase or event, decision=answer.get('decision'),
           termination=answer.get('terminationBehavior'))
    return answer

def proxy_status():
    requests = [dict(jsonrpc='2.0', id=1, method='initialize'),
        dict(jsonrpc='2.0', id=2, method='tools/call',
             params=dict(name='runtime_status', arguments={}))]
    record('proxy-start', phase='runtime_status')
    result = subprocess.run([mcp['command'], *mcp['args']],
        env=dict(os.environ, **mcp['env']), cwd=mcp['cwd'],
        input=''.join(json.dumps(row) + '\n' for row in requests),
        capture_output=True, text=True, timeout=30)
    record('proxy-exit', phase='runtime_status', returncode=result.returncode)
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout.splitlines()[-1])
    assert not reply['result'].get('isError'), reply
    return json.loads(reply['result']['content'][0]['text'])

record('native', pid=os.getpid(), session=session)
for index, raw in enumerate(sys.stdin, 1):
    message = json.loads(raw)
    assert message['event'] == 'user'
    content = message['message']['content']
    record('input', index=index, content=content,
           native_ready=(state / 'session.json').exists(),
           hook_ready=(state / 'antigravity/hook-session.json').exists())
    if index == 1:
        # Exercise init arriving while the real SessionStart subprocess is still pending.
        if mode != 'missing-init':
            emit('init', conversation_id=session)
        if mode != 'missing-hook':
            observed = hook('SessionStart')
            assert observed.get('terminationBehavior') != 'terminate', observed
            record('session-hook', result=observed)
    if mode == 'normal':
        checked = hook('PreInvocation')
        assert checked.get('terminationBehavior') != 'terminate', checked
    if index == 2 and os.environ.get('AGY_TEST_GUARDS') == '1':
        decisions = {}
        for name in ('owned.txt', 'unreserved.txt', 'foreign.txt'):
            decisions[name] = hook('PreToolUse', dict(toolCall=dict(name='write_to_file',
                args=dict(TargetFile=str(pathlib.Path.cwd() / name)))), phase=name)
        decisions['foreign-context'] = hook('PreToolUse', dict(toolCall=dict(name='write_to_file',
            args=dict(TargetFile=str(pathlib.Path.cwd() / 'owned.txt')))),
            {'AGENTSTACK_FRESH_CLIENT_CONTEXT': os.environ['AGY_TEST_OTHER_CONTEXT']},
            phase='foreign-context')
        decisions['notebook_edit'] = hook('PreToolUse',
            dict(toolCall=dict(name='notebook_edit', args={})), phase='notebook_edit')
        record('guard-proxy', decisions=decisions, status=proxy_status())
    emit('result', result=dict(status='SUCCESS', response='READY' if index == 1 else content,
                               denied_actions=[]))
    record('result', index=index)
'''


def trace_rows(path):
    if not path.exists():return []
    raw = path.read_text()
    complete = raw.rsplit('\n', 1)[0]
    return [json.loads(line) for line in complete.splitlines() if line]


async def until(check, process, log, timeout=30, *, trace=None):
    def diagnostic():
        tail = [] if trace is None else [
            {key: row[key] for key in ('kind', 'monotonic', 'event', 'phase', 'returncode', 'decision', 'termination')
             if key in row} for row in trace_rows(trace)[-12:]]
        return log.read_text() + '\nRecent native phases: ' + json.dumps(tail)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:return result
        assert process.returncode is None, diagnostic()
        await asyncio.sleep(0.05)
    pytest.fail('native fixture timed out: ' + diagnostic())


@asynccontextmanager
async def launched(parent, work, monkeypatch, *, mode='normal', guards=False):
    binary = work / 'synthetic-agy'
    binary.write_text('#!' + sys.executable + '\n' + NATIVE)
    binary.chmod(0o700)
    monkeypatch.setenv('AGENTSTACK_GEMINI_BIN', str(binary))
    prepared = await prepare(parent, 'native-agy', 'NativeAgy', 'antigravity',
        'synthetic-native-model', 'native-shaped integration fixture', effort='high')
    client = FreshBoundClient(prepared['context_path'])
    task = 'User task nonce ' + uuid4().hex
    transport = SyntheticTransport(work / 'not-used.sock')
    await handoff(parent, ROOT, slot='native-agy', cwd=work, task=task,
        resources=['owned.txt'], transport=transport)
    argv = shlex.split(transport.calls[0][-1])
    trace, log = work / 'native-trace.jsonl', work / 'launch.log'
    env = environment(client, AGENTSTACK_RUNTIME_MODE='fresh',
        AGY_TEST_TRACE=str(trace), AGY_TEST_MODE=mode, AGY_TEST_GUARDS='1' if guards else '0',
        AGY_TEST_OTHER_CONTEXT=str(parent.path))
    env.pop('TMUX', None);env.pop('TMUX_PANE', None)
    with log.open('w') as output:
        process = await asyncio.create_subprocess_exec(*argv, cwd=work, env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        try:
            yield client, process, trace, log, task
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGINT)
                try:await asyncio.wait_for(process.wait(), timeout=8)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.wait_for(process.wait(), timeout=5)


def native_run(client):
    base = client.path.parent / 'launch'
    return base / 'runs' / _read_private(base / 'current.json')['launch_id']


async def stop_runner(client, process, log):
    folder = native_run(client)
    socket_record = _read_private(folder / 'antigravity/socket.json')
    path = Path(socket_record['path'])
    os.kill(socket_record['runner_pid'], signal.SIGINT)
    assert await asyncio.wait_for(process.wait(), timeout=15) == 130, log.read_text()
    assert not path.exists() and not path.parent.exists()
    assert _read_private(folder / 'ended.json')['exit_code'] == 130


@pytest.mark.parametrize('mode', ['missing-hook', 'missing-init'])
def test_native_bootstrap_requires_real_hook_and_init_before_user_task(tmp_path, monkeypatch, mode):
    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            work = tmp_path / 'work';work.mkdir()
            async with launched(parent, work, monkeypatch, mode=mode) as (client, process, trace, log, task):
                assert await asyncio.wait_for(process.wait(), timeout=45) != 0
                received = [r for r in trace_rows(trace) if r['kind'] == 'input']
                assert len(received) == 1 and task not in received[0]['content']
                folder = native_run(client)
                assert not (folder / 'session.json').exists()
                socket_record = _read_private(folder / 'antigravity/socket.json')
                assert not Path(socket_record['path']).exists()
                assert 'FRESH_ANTIGRAVITY_HOOK_UNCONFIRMED' in log.read_text()
                # Failed bootstrap must return only this child's existing lease.
                await parent.call('reserve_files', {'paths': [str(work / 'owned.txt')]})
    asyncio.run(run())


def test_native_hooks_proxy_identity_and_reservations_survive_until_sigint(tmp_path, monkeypatch):
    async def run():
        async with live(tmp_path) as (runtime, people, parent, _):
            work = tmp_path / 'work';work.mkdir()
            await grant(runtime, people, [work / 'foreign.txt'], actor=1)
            async with launched(parent, work, monkeypatch, guards=True) as (client, process, trace, log, task):
                # This turn starts five real hook clients and one stdio MCP client sequentially.
                await until(lambda: 'native turn 1: SUCCESS' in log.read_text(), process, log,
                            timeout=60, trace=trace)
                received = [r for r in trace_rows(trace) if r['kind'] == 'input']
                assert len(received) == 2 and task in received[1]['content']
                assert received[1]['native_ready'] and received[1]['hook_ready']
                evidence = next(r for r in trace_rows(trace) if r['kind'] == 'guard-proxy')
                decisions = evidence['decisions']
                assert decisions['owned.txt']['decision'] == 'allow'
                assert all(decisions[name]['decision'] == 'deny' for name in
                           ('unreserved.txt', 'foreign.txt', 'foreign-context', 'notebook_edit'))
                assert 'FRESH_ANTIGRAVITY_EDITOR_UNSUPPORTED' in decisions['notebook_edit']['reason']
                assert evidence['status']['mail_agent_id'] == client.context['mail_agent_id']
                assert evidence['status']['mail_instance_id'] == parent.context['mail_instance_id']
                _, base, _ = family(client)
                assert status(client, base)['native_ready']
                with pytest.raises(B1Error, match='RESERVATION_CONFLICT'):
                    await parent.call('reserve_files', {'paths': [str(work / 'owned.txt')]})
                assert not any((work / name).exists() for name in ('owned.txt', 'unreserved.txt', 'foreign.txt'))
                await stop_runner(client, process, log)
                await parent.call('reserve_files', {'paths': [str(work / 'owned.txt')]})
                with pytest.raises(B1Error, match='RESERVATION_CONFLICT'):
                    await parent.call('reserve_files', {'paths': [str(work / 'foreign.txt')]})
    asyncio.run(run())


def test_native_mail_wakeup_runs_another_turn_without_inventing_read_or_ack(tmp_path, monkeypatch):
    async def run():
        async with live(tmp_path) as (runtime, _, parent, _):
            work = tmp_path / 'work';work.mkdir()
            async with launched(parent, work, monkeypatch) as (client, process, trace, log, task):
                await until(lambda: 'native turn 1: SUCCESS' in log.read_text(), process, log, trace=trace)
                nonce = uuid4().hex
                sent = await parent.call('send_message', {
                    'recipients': [{'target_agent_id': client.context['mail_agent_id']}],
                    'subject': 'Fresh native continuation', 'body_md': 'Second task nonce ' + nonce,
                    'importance': 'high', 'ack_required': True})
                folder = native_run(client)
                intent = _read_private(folder / 'intent.json')
                socket_record = _read_private(folder / 'antigravity/socket.json')
                assert await asyncio.to_thread(_exchange, socket_record,
                    {'launch_id': folder.name, 'nonce': 'different-launch'}) is False
                before, _ = await rows(runtime)
                fact = next(row for row in before if row['message_id'] == sent['message_id'])
                assert fact['completed_ts'] is None
                assert await asyncio.to_thread(_exchange, socket_record,
                    {'launch_id': folder.name, 'nonce': intent['nonce']}) is True
                await until(lambda: 'native turn 2: SUCCESS' in log.read_text(), process, log, trace=trace)
                received = [r for r in trace_rows(trace) if r['kind'] == 'input']
                assert len(received) == 3 and nonce in received[2]['content']
                assert nonce not in received[1]['content'] and task not in received[2]['content']
                facts, recipients = await rows(runtime)
                fact = next(row for row in facts if row['message_id'] == sent['message_id'])
                assert fact['completed_ts'] is not None
                recipient = next(row for row in recipients if row[0] == sent['message_id'])
                assert recipient == (sent['message_id'], client.context['mail_agent_id'], None, None)
                assert await asyncio.to_thread(_exchange, socket_record,
                    {'launch_id': folder.name, 'nonce': intent['nonce']}) is False
                await stop_runner(client, process, log)
    asyncio.run(run())


def test_native_resume_keeps_session_index_and_does_not_repeat_original_task(tmp_path, monkeypatch):
    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            work = tmp_path / 'work';work.mkdir()
            async with launched(parent, work, monkeypatch) as (client, first, trace, first_log, task):
                await until(lambda: 'native turn 1: SUCCESS' in first_log.read_text(),
                            first, first_log, trace=trace)
                original_run = native_run(client)
                original = _read_private(original_run / 'session.json')
                session_id = original['native_session_id']
                index = client.path.parent / 'launch/sessions' / (session_id + '.json')
                saved_index = index.read_bytes()
                await stop_runner(client, first, first_log)
                resume_trace, resume_log = work / 'resume-trace.jsonl', work / 'resume.log'
                env = environment(client, AGENTSTACK_RUNTIME_MODE='fresh',
                    AGENTSTACK_GEMINI_BIN=str(work / 'synthetic-agy'),
                    AGY_TEST_TRACE=str(resume_trace), AGY_TEST_MODE='normal', AGY_TEST_GUARDS='0')
                env.pop('TMUX', None);env.pop('TMUX_PANE', None)
                argv = [sys.executable, '-m', 'agentstack_mail.fresh_client', 'resume',
                        '--context', str(client.path), '--wrapper-root', str(ROOT),
                        'antigravity', str(work), '--session-id', session_id]
                with resume_log.open('w') as output:
                    resumed = await asyncio.create_subprocess_exec(*argv, cwd=work, env=env,
                        stdin=asyncio.subprocess.DEVNULL, stdout=output,
                        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
                    try:
                        await until(lambda: 'resumed; waiting for Mail' in resume_log.read_text(),
                                    resumed, resume_log, timeout=45, trace=resume_trace)
                        new_run = native_run(client)
                        assert new_run.name != original_run.name
                        current = _read_private(new_run / 'session.json')
                        assert current['native_session_id'] == session_id
                        assert current['transcript_path'] == original['transcript_path']
                        assert index.read_bytes() == saved_index
                        inputs = [row for row in trace_rows(resume_trace) if row['kind'] == 'input']
                        assert len(inputs) == 1 and task not in inputs[0]['content']
                        assert _read_private(new_run / 'antigravity/plan.json')['prompt'] == ''
                        await stop_runner(client, resumed, resume_log)
                    finally:
                        if resumed.returncode is None:
                            os.killpg(resumed.pid, signal.SIGINT)
                            try:await asyncio.wait_for(resumed.wait(), timeout=8)
                            except asyncio.TimeoutError:
                                os.killpg(resumed.pid, signal.SIGKILL)
                                await asyncio.wait_for(resumed.wait(), timeout=5)
    asyncio.run(run())
