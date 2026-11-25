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
import runpy
import signal
import sys
import time
from uuid import uuid4

import pytest

from agentstack_mail import fresh_antigravity as agy
from agentstack_mail.fresh_antigravity import _exchange
from agentstack_mail.fresh_child import family, handoff, prepare, status
from agentstack_mail.fresh_client import FreshBoundClient
from agentstack_mail.fresh_runtime import _read_private
from agentstack_mail.storage_origin_contract import B1Error, digest
from test_fresh_antigravity_core import SyntheticTransport
from test_fresh_guard import grant, live
from test_fresh_notifications import rows
from test_fresh_proxy import environment

ROOT = Path(__file__).resolve().parents[3]

NATIVE = r'''
import json, os, pathlib, re, signal, subprocess, sys, time, uuid

args = sys.argv[1:]
assert args[args.index('--input-format') + 1] == 'stream-json'
assert args[args.index('--output-format') + 1] == 'stream-json'
assert not {'--mode', '--dangerously-skip-permissions'} & set(args)
integration = pathlib.Path(args[args.index('--add-dir') + 1])
resuming = '--conversation' in args
session = args[args.index('--conversation') + 1] if resuming else str(uuid.uuid4())
trace = pathlib.Path(os.environ['AGY_TEST_TRACE'])
mode = os.environ.get('AGY_TEST_MODE', 'normal')
state = pathlib.Path(os.environ['AGENTSTACK_FRESH_CLIENT_CONTEXT']).parent / 'launch/runs' / os.environ['AGENTSTACK_FRESH_LAUNCH_ID']
conversations = pathlib.Path(os.environ['AGY_TEST_CONVERSATIONS'])
conversations.mkdir(mode=0o700, exist_ok=True)
conversation = conversations / (session + '.json')
workspaces = json.loads(conversation.read_text())['workspacePaths'] if resuming else [str(integration), os.getcwd()]
workspaces = list(dict.fromkeys([*workspaces, str(integration)]))
conversation.write_text(json.dumps(dict(workspacePaths=workspaces)))
conversation.chmod(0o600)
# Native resume restores paths, rereads their configurations, and omits SessionStart.
sources = workspaces if os.environ.get('AGY_TEST_HISTORY_ORDER') == 'old-first' else [
    str(integration), *[path for path in workspaces if path != str(integration)]]
groups, servers = [], {}
for source in sources:
    folder = pathlib.Path(source) / '.agents'
    hooks_path = folder / 'hooks.json'
    if hooks_path.exists() and not (source == str(integration)
            and os.environ.get('AGY_TEST_SKIP_CURRENT_HOOKS') == '1'):
        groups.extend((source, name, hooks) for name, hooks in json.loads(hooks_path.read_text()).items())
    mcp_path = folder / 'mcp_config.json'
    if mcp_path.exists():
        servers.update(json.loads(mcp_path.read_text())['mcpServers'])
assert len(servers) == 1
mcp = next(iter(servers.values()))

def record(kind, **values):
    with trace.open('a') as output:
        output.write(json.dumps(dict(kind=kind, monotonic=time.monotonic(), **values)) + '\n')

def emit(kind, **values):
    print(json.dumps(dict(event=kind, **values)), flush=True)

def hook(event, extra=None, env_changes=None, *, phase=None):
    payload = dict(conversationId=session, workspacePaths=workspaces)
    payload.update(extra or {})
    combined = {}
    matched = False
    for source, group_name, hooks in groups:
        handlers = hooks.get(event, [])
        if event in ('PreToolUse', 'PostToolUse'):
            name = payload['toolCall']['name']
            handlers = [handler for group in handlers
                        if group.get('matcher', '') in ('', '*') or re.search(group['matcher'], name)
                        for handler in group['hooks']]
        for handler in handlers:
            matched = True
            details = dict(event=event, phase=phase or event, source=source, group=group_name)
            record('hook-start', **details)
            result = subprocess.run(['/bin/bash', '-c', handler['command']], input=json.dumps(payload),
                cwd=os.getcwd(), env=dict(os.environ, **(env_changes or {})), capture_output=True,
                text=True, timeout=30)
            record('hook-exit', returncode=result.returncode, **details)
            assert result.returncode == 0, result.stderr
            answer = json.loads(result.stdout)
            record('hook-result', answer=answer, decision=answer.get('decision'),
                   termination=answer.get('terminationBehavior'),
                   hook_ready=(state / 'antigravity/hook-session.json').exists(), **details)
            if answer.get('decision') == 'deny' or answer.get('terminationBehavior') == 'terminate':
                return answer
            if answer.get('injectSteps'):
                combined.setdefault('injectSteps', []).extend(answer['injectSteps'])
            combined.update({key: value for key, value in answer.items() if key != 'injectSteps'})
    if not matched:
        record('hook-unmatched', event=event, phase=phase or event,
               tool=payload.get('toolCall', {}).get('name'))
    return combined

def continue_after(answer):
    if answer.get('terminationBehavior') != 'terminate':
        return True
    response = '\n'.join(step['errorMessage']['userMessage'] for step in answer.get('injectSteps', [])
                         if 'errorMessage' in step)
    emit('result', result=dict(status='ERROR', response=response, denied_actions=[]))
    record('result-error', response=response)
    return False

def proxy_call(name, arguments):
    requests = [dict(jsonrpc='2.0', id=1, method='initialize'),
        dict(jsonrpc='2.0', id=2, method='tools/call',
             params=dict(name=name, arguments=arguments))]
    record('proxy-start', phase=name)
    result = subprocess.run([mcp['command'], *mcp['args']],
        env=dict(os.environ, **mcp['env']), cwd=mcp['cwd'],
        input=''.join(json.dumps(row) + '\n' for row in requests),
        capture_output=True, text=True, timeout=30)
    record('proxy-exit', phase=name, returncode=result.returncode)
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout.splitlines()[-1])
    assert not reply['result'].get('isError'), reply
    return json.loads(reply['result']['content'][0]['text'])

record('native', pid=os.getpid(), session=session, workspaces=workspaces,
       hook_sources=[source for source, _, _ in groups])
for index, raw in enumerate(sys.stdin, 1):
    message = json.loads(raw)
    assert message['event'] == 'user'
    content = message['message']['content']
    record('input', index=index, content=content,
           native_ready=(state / 'session.json').exists(),
           hook_ready=(state / 'antigravity/hook-session.json').exists())
    if index == 1:
        if os.environ.get('AGY_TEST_BOOTSTRAP_GATE'):
            gate = pathlib.Path(os.environ['AGY_TEST_BOOTSTRAP_GATE'])
            deadline = time.monotonic() + 45
            while not gate.exists():
                assert time.monotonic() < deadline, 'bootstrap gate was not released'
                time.sleep(.01)
        # Exercise init arriving while the real SessionStart subprocess is still pending.
        if mode != 'missing-init':
            emit('init', conversation_id=session)
        if mode != 'missing-hook' and not resuming:
            observed = hook('SessionStart')
            if not continue_after(observed):
                break
            record('session-hook', result=observed)
    if resuming and index == 1 and os.environ.get('AGY_TEST_SKIP_CURRENT_HOOKS') == '1':
        blocked = hook('PreToolUse', dict(toolCall=dict(name='write_file',
            args=dict(Path=str(pathlib.Path.cwd() / 'owned.txt')))), phase='pre-ready-guard')
        record('pre-ready-guard', result=blocked)
    checked = hook('PreInvocation')
    if not continue_after(checked):
        break
    if index == 2 and os.environ.get('AGY_TEST_GUARDS') == '1':
        decisions = {}
        for tool in ('write_file', 'edit_file'):
            for name in ('owned.txt', 'unreserved.txt', 'foreign.txt'):
                phase = tool + '/' + name
                decisions[phase] = hook('PreToolUse', dict(toolCall=dict(name=tool,
                    args=dict(Path=str(pathlib.Path.cwd() / name)))), phase=phase)
        decisions['foreign-context'] = hook('PreToolUse', dict(toolCall=dict(name='write_file',
            args=dict(Path=str(pathlib.Path.cwd() / 'owned.txt')))),
            {'AGENTSTACK_FRESH_CLIENT_CONTEXT': os.environ['AGY_TEST_OTHER_CONTEXT']},
            phase='foreign-context')
        for unsupported in ('notebook_edit', 'sed_file'):
            decisions[unsupported] = hook('PreToolUse',
                dict(toolCall=dict(name=unsupported, args={})), phase=unsupported)
        record('guard-proxy', decisions=decisions, status=proxy_call('runtime_status', {}))
    if resuming and index == 2 and os.environ.get('AGY_TEST_RESUME_MAIL') == '1':
        decisions = {name: hook('PreToolUse', dict(toolCall=dict(name='write_file',
            args=dict(Path=str(pathlib.Path.cwd() / name)))), phase='resume/' + name)
            for name in ('owned.txt', 'unreserved.txt')}
        owner = proxy_call('runtime_status', {})
        inbox = proxy_call('fetch_inbox', {})
        message = next(row for row in inbox if row['subject'] == 'Native resume continuation')
        reply = proxy_call('reply_message', {'message_id': message['id'], 'body_md': message['body_md']})
        record('resume-proxy', status=owner, reply=reply, decisions=decisions)
    if mode == 'active-crash' and index == 2:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        member = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
        record('active-crash', member_pid=member.pid)
        while True:
            time.sleep(.1)
    emit('result', result=dict(status='SUCCESS', response='READY' if index == 1 else content,
                               denied_actions=[]))
    record('result', index=index)
'''


def trace_rows(path):
    if not path.exists():return []
    raw = path.read_bytes()
    complete = raw.rsplit(b'\n', 1)[0] if b'\n' in raw else b''
    return [json.loads(line) for line in complete.splitlines() if line]


async def until(check, process, log, timeout=60, *, trace=None):
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
        PYTHONPATH=os.pathsep.join([str(ROOT / 'packages/agentstack_mail/src'), str(ROOT)]),
        AGY_TEST_TRACE=str(trace), AGY_TEST_MODE=mode, AGY_TEST_GUARDS='1' if guards else '0',
        AGY_TEST_CONVERSATIONS=str(work / 'native-conversations'),
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


@asynccontextmanager
async def native_resume(client, work, session_id, *, order='new-first', gate=None, missing_current=False, label='resume'):
    trace, log = work / (label + '-trace.jsonl'), work / (label + '.log')
    env = environment(client, AGENTSTACK_RUNTIME_MODE='fresh',
        PYTHONPATH=os.pathsep.join([str(ROOT / 'packages/agentstack_mail/src'), str(ROOT)]),
        AGENTSTACK_GEMINI_BIN=str(work / 'synthetic-agy'),
        AGY_TEST_TRACE=str(trace), AGY_TEST_MODE='normal', AGY_TEST_GUARDS='0',
        AGY_TEST_CONVERSATIONS=str(work / 'native-conversations'), AGY_TEST_HISTORY_ORDER=order,
        AGY_TEST_RESUME_MAIL='1', AGY_TEST_SKIP_CURRENT_HOOKS='1' if missing_current else '0')
    if gate is not None:env['AGY_TEST_BOOTSTRAP_GATE'] = str(gate)
    env.pop('TMUX', None);env.pop('TMUX_PANE', None)
    argv = [sys.executable, '-m', 'agentstack_mail.fresh_client', 'resume',
            '--context', str(client.path), '--wrapper-root', str(ROOT),
            'antigravity', str(work), '--session-id', session_id]
    with log.open('w') as output:
        process = await asyncio.create_subprocess_exec(*argv, cwd=work, env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=output,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        try:
            yield process, trace, log
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGINT)
                try:await asyncio.wait_for(process.wait(), timeout=8)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.wait_for(process.wait(), timeout=5)


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
                if mode == 'missing-hook':
                    failure = next(row for row in trace_rows(trace) if row['kind'] == 'result-error')
                    assert 'FRESH_NATIVE_SESSION_MISMATCH' in failure['response']
                    assert 'FRESH_ANTIGRAVITY_TURN_INCOMPLETE' in log.read_text()
                else:
                    assert 'FRESH_ANTIGRAVITY_HOOK_UNCONFIRMED' in log.read_text()
                ended_path = folder / 'antigravity/native-ended.json'
                assert ended_path.is_file(), log.read_text()
                ended = _read_private(ended_path)
                assert (ended.get('launch_id'), ended.get('context_digest')) == (
                    folder.name, client.context_digest), log.read_text()
                # Failed bootstrap must return only this child's existing lease.
                await parent.call('reserve_files', {'paths': [str(work / 'owned.txt')]})
    asyncio.run(run())


def test_native_hooks_proxy_identity_and_reservations_survive_until_sigint(tmp_path, monkeypatch):
    async def run():
        async with live(tmp_path) as (runtime, people, parent, _):
            work = tmp_path / 'work';work.mkdir()
            await grant(runtime, people, [work / 'foreign.txt'], actor=1)
            async with launched(parent, work, monkeypatch, guards=True) as (client, process, trace, log, task):
                # SessionStart, two PreInvocation callbacks and nine guards use subprocesses;
                # allow their cumulative startup time on a loaded Mac runner.
                await until(lambda: 'native turn 1: SUCCESS' in log.read_text(), process, log,
                            timeout=180, trace=trace)
                received = [r for r in trace_rows(trace) if r['kind'] == 'input']
                assert len(received) == 2 and task in received[1]['content']
                assert received[1]['native_ready'] and received[1]['hook_ready']
                startup = next(r['result'] for r in trace_rows(trace) if r['kind'] == 'session-hook')
                instruction = startup['injectSteps'][0]['systemMessage']['systemMessage']
                assert 'not the native send_message tool' in instruction
                assert '"target_agent_id"' in instruction and 'body_md' in instruction
                assert '"message_id"' in instruction
                evidence = next(r for r in trace_rows(trace) if r['kind'] == 'guard-proxy')
                decisions = evidence['decisions']
                for tool in ('write_file', 'edit_file'):
                    assert decisions[tool + '/owned.txt']['decision'] == 'allow'
                    assert all(decisions[tool + '/' + name]['decision'] == 'deny'
                               for name in ('unreserved.txt', 'foreign.txt'))
                assert all(decisions[name]['decision'] == 'deny' for name in
                           ('foreign-context', 'notebook_edit', 'sed_file'))
                assert 'FRESH_ANTIGRAVITY_EDITOR_UNSUPPORTED' in decisions['notebook_edit']['reason']
                assert 'FRESH_ANTIGRAVITY_EDITOR_UNSUPPORTED' in decisions['sed_file']['reason']
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


@pytest.mark.parametrize('order', ['new-first', 'old-first'])
def test_native_resume_keeps_session_index_and_does_not_repeat_original_task(tmp_path, monkeypatch, order):
    async def run():
        async with live(tmp_path) as (runtime, _, parent, _):
            work = tmp_path / 'work';work.mkdir()
            async with launched(parent, work, monkeypatch) as (client, first, trace, first_log, task):
                await until(lambda: 'native turn 1: SUCCESS' in first_log.read_text(),
                            first, first_log, trace=trace)
                original_run = native_run(client)
                original = _read_private(original_run / 'session.json')
                session_id = original['native_session_id']
                index = client.path.parent / 'launch/sessions' / (session_id + '.json')
                saved_index = index.read_bytes()
                original_hook = (original_run / 'antigravity/hook-session.json').read_bytes()
                await stop_runner(client, first, first_log)
                await client.call('reserve_files', {'paths': [str(work / 'owned.txt')]})
                async with native_resume(client, work, session_id, order=order) as (resumed, resume_trace, resume_log):
                    await until(lambda: 'resumed; waiting for Mail' in resume_log.read_text(),
                                resumed, resume_log, timeout=90, trace=resume_trace)
                    new_run = native_run(client)
                    assert new_run.name != original_run.name
                    current = _read_private(new_run / 'session.json')
                    assert current['native_session_id'] == session_id
                    assert current['transcript_path'] == original['transcript_path']
                    assert index.read_bytes() == saved_index
                    assert (original_run / 'antigravity/hook-session.json').read_bytes() == original_hook
                    plan_path = new_run / 'antigravity/plan.json'
                    plan = _read_private(plan_path)
                    assert plan['prompt'] == ''
                    origin = _read_private(new_run / 'antigravity/hook-origin.json')
                    assert origin == dict(_read_private(new_run / 'antigravity/hook-session.json'),
                        event='PreInvocation', source_plan_path=str(plan_path), source_plan_digest=digest(plan))
                    observed = trace_rows(resume_trace)
                    native = next(row for row in observed if row['kind'] == 'native')
                    previous_path = str(original_run / 'antigravity/integration')
                    current_path = str(new_run / 'antigravity/integration')
                    assert native['workspaces'] == [previous_path, str(work), current_path]
                    expected_order = [current_path, previous_path] if order == 'new-first' else [previous_path, current_path]
                    assert native['hook_sources'] == expected_order
                    assert not any(row.get('event') == 'SessionStart' for row in observed)
                    callbacks = [row for row in observed if row['kind'] == 'hook-result'
                                 and row['event'] == 'PreInvocation']
                    assert [row['source'] for row in callbacks] == expected_order
                    assert all(row['termination'] is None for row in callbacks)
                    if order == 'old-first':assert callbacks[0]['hook_ready'] is False
                    assert callbacks[-1]['hook_ready'] is True
                    inputs = [row for row in observed if row['kind'] == 'input']
                    assert len(inputs) == 1 and task not in inputs[0]['content']

                    nonce = uuid4().hex
                    sent = await parent.call('send_message', {
                        'recipients': [{'target_agent_id': client.context['mail_agent_id']}],
                        'subject': 'Native resume continuation', 'body_md': nonce,
                        'importance': 'high', 'ack_required': True})
                    intent = _read_private(new_run / 'intent.json')
                    socket_record = _read_private(new_run / 'antigravity/socket.json')
                    assert await asyncio.to_thread(_exchange, socket_record,
                        {'launch_id': new_run.name, 'nonce': intent['nonce']}) is True
                    await until(lambda: 'native turn 1: SUCCESS' in resume_log.read_text(),
                                resumed, resume_log, timeout=120, trace=resume_trace)
                    observed = trace_rows(resume_trace)
                    proof = next(row for row in observed if row['kind'] == 'resume-proxy')
                    assert proof['status']['mail_agent_id'] == client.context['mail_agent_id']
                    assert proof['status']['mail_instance_id'] == parent.context['mail_instance_id']
                    assert proof['decisions']['owned.txt']['decision'] == 'allow'
                    assert proof['decisions']['unreserved.txt']['decision'] == 'deny'
                    assert 'RESERVATION_REQUIRED' in proof['decisions']['unreserved.txt']['reason']
                    guards = [row for row in observed if row['kind'] == 'hook-result'
                              and row['phase'] == 'resume/owned.txt']
                    assert [row['source'] for row in guards] == expected_order
                    assert all(row['decision'] == 'allow' for row in guards)
                    inputs = [row for row in observed if row['kind'] == 'input']
                    assert len(inputs) == 2 and nonce in inputs[1]['content']
                    assert all(task not in row['content'] for row in inputs)
                    inbox = await parent.call('fetch_inbox', {})
                    assert any(row['body_md'] == nonce and row['sender_id'] == client.context['mail_agent_id']
                               and row['id'] == proof['reply']['message_id'] for row in inbox)
                    facts, recipients = await rows(runtime)
                    assert next(row for row in facts if row['message_id'] == sent['message_id'])['completed_ts'] is not None
                    assert next(row for row in recipients if row[0] == sent['message_id']) == (
                        sent['message_id'], client.context['mail_agent_id'], None, None)
                    assert index.read_bytes() == saved_index
                    await stop_runner(client, resumed, resume_log)
    asyncio.run(run())


@pytest.mark.parametrize('boundary', ['missing-current', 'foreign-session', 'unended-history'])
def test_native_resume_refuses_unproven_history_without_task_or_mail(tmp_path, monkeypatch, boundary):
    async def run():
        async with live(tmp_path) as (runtime, _, parent, _):
            work = tmp_path / 'work';work.mkdir()
            async with launched(parent, work, monkeypatch) as (client, first, trace, first_log, task):
                await until(lambda: 'native turn 1: SUCCESS' in first_log.read_text(),
                            first, first_log, trace=trace)
                original = native_run(client)
                session_id = _read_private(original / 'session.json')['native_session_id']
                await stop_runner(client, first, first_log)
                gate = work / 'resume-bootstrap-gate'
                async with native_resume(client, work, session_id, gate=gate, order='old-first',
                        missing_current=boundary == 'missing-current') as (resumed, resume_trace, resume_log):
                    await until(lambda: any(row['kind'] == 'input' for row in trace_rows(resume_trace)),
                                resumed, resume_log, timeout=45, trace=resume_trace)
                    # Change the old source after launch admission, before its actual callback.
                    if boundary == 'foreign-session':
                        path = original / 'antigravity/hook-session.json'
                        evidence = _read_private(path)
                        path.write_text(json.dumps(dict(evidence, native_session_id=str(uuid4()))))
                    elif boundary == 'unended-history':
                        (original / 'ended.json').unlink()
                    folder = native_run(client)
                    nonce = uuid4().hex
                    sent = await parent.call('send_message', {
                        'recipients': [{'target_agent_id': client.context['mail_agent_id']}],
                        'subject': 'Native resume continuation', 'body_md': nonce, 'importance': 'high'})
                    intent = _read_private(folder / 'intent.json')
                    socket_record = _read_private(folder / 'antigravity/socket.json')
                    assert await asyncio.to_thread(_exchange, socket_record,
                        {'launch_id': folder.name, 'nonce': intent['nonce']}) is False
                    gate.touch()
                    assert await asyncio.wait_for(resumed.wait(), timeout=90) != 0, resume_log.read_text()
                    observed = trace_rows(resume_trace)
                    inputs = [row for row in observed if row['kind'] == 'input']
                    assert len(inputs) == 1 and task not in inputs[0]['content'] and nonce not in inputs[0]['content']
                    assert not (folder / 'session.json').exists()
                    assert not (folder / 'antigravity/native-session.json').exists()
                    assert not any(row['kind'] == 'proxy-start' for row in observed)
                    assert 'resumed; waiting for Mail' not in resume_log.read_text()
                    if boundary == 'missing-current':
                        assert not (folder / 'antigravity/hook-session.json').exists()
                        assert not (folder / 'antigravity/hook-origin.json').exists()
                        blocked = next(row['result'] for row in observed if row['kind'] == 'pre-ready-guard')
                        assert blocked['decision'] == 'deny' and 'FRESH_NATIVE_SESSION_MISMATCH' in blocked['reason']
                        assert 'FRESH_ANTIGRAVITY_HOOK_UNCONFIRMED' in resume_log.read_text()
                    else:
                        error = next(row for row in observed if row['kind'] == 'result-error')
                        expected = ('FRESH_NATIVE_SESSION_MISMATCH' if boundary == 'foreign-session'
                                    else 'FRESH_LAUNCH_OUTCOME_UNKNOWN')
                        assert expected in error['response']
                        assert 'FRESH_ANTIGRAVITY_TURN_INCOMPLETE' in resume_log.read_text()
                    facts, _ = await rows(runtime)
                    assert next(row for row in facts if row['message_id'] == sent['message_id'])['completed_ts'] is None
                    assert (folder / 'ended.json').exists()
                if boundary == 'missing-current':
                    # A failed resume remains in native workspace history without ready proof.
                    async with native_resume(client, work, session_id, label='recovered') as (recovered, recovery_trace, recovery_log):
                        await until(lambda: 'resumed; waiting for Mail' in recovery_log.read_text(),
                                    recovered, recovery_log, timeout=120, trace=recovery_trace)
                        recovered_run = native_run(client)
                        native = next(row for row in trace_rows(recovery_trace) if row['kind'] == 'native')
                        assert native['workspaces'] == [str(original / 'antigravity/integration'), str(work),
                            str(folder / 'antigravity/integration'), str(recovered_run / 'antigravity/integration')]
                        assert len(native['hook_sources']) == 3
                        assert _read_private(recovered_run / 'session.json')['native_session_id'] == session_id
                        inputs = [row for row in trace_rows(recovery_trace) if row['kind'] == 'input']
                        assert len(inputs) == 1 and task not in inputs[0]['content'] and nonce not in inputs[0]['content']
                        await stop_runner(client, recovered, recovery_log)
    asyncio.run(run())


def test_historical_hook_policy_only_normalizes_launch_identifiers(tmp_path):
    async def run():
        async with live(tmp_path) as (_, _, client, _):
            plans = []
            settings = {'binary': str(tmp_path / 'synthetic-agy'), 'model': 'synthetic-native-model', 'effort': 'high'}
            for _ in range(2):
                folder = tmp_path / str(uuid4());folder.mkdir(mode=0o700)
                agy.plan(client, ROOT, settings, folder, tmp_path, '', None)
                plans.append(_read_private(folder / 'antigravity/plan.json'))
            source, current = plans
            assert agy.same_hook_policy(source, current)
            directory = Path(source['integration_dir']) / '.agents'
            for difference in ('matcher', 'timeout', 'mcp-env'):
                path = directory / ('mcp_config.json' if difference == 'mcp-env' else 'hooks.json')
                saved = path.read_bytes()
                config = _read_private(path)
                if difference == 'mcp-env':
                    next(iter(config['mcpServers'].values()))['env']['AGY_TEST_POLICY'] = 'changed'
                else:
                    group = next(iter(config.values()))
                    if difference == 'matcher':group['PreToolUse'][0]['matcher'] += '|another_editor'
                    else:group['PreInvocation'][0]['timeout'] += 1
                try:
                    path.write_text(json.dumps(config))
                    assert agy.same_hook_policy(source, current) is False, difference
                finally:
                    path.write_bytes(saved)
            assert agy.same_hook_policy(source, current)
    asyncio.run(run())


def test_runner_sigkill_stops_active_native_group_without_releasing_lease(tmp_path, monkeypatch):
    from agentstack_mail.fresh_antigravity_end import verify_native_ended

    api = runpy.run_path(str(ROOT / 'bin/lib/process_observation.py'))

    def rows():
        result = api['process_snapshot'](include_groups=True)
        assert isinstance(result, dict) and result
        return result

    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            work = tmp_path / 'work';work.mkdir()
            async with launched(parent, work, monkeypatch, mode='active-crash') as (client, process, trace, log, _):
                active = await until(lambda: next((r for r in trace_rows(trace)
                    if r['kind'] == 'active-crash'), None), process, log, trace=trace)
                folder = native_run(client)
                native = _read_private(folder / 'antigravity/native-process.json')
                runner = native['runner_pid']
                pid = native['pid']
                before = rows()
                known = {key: row for key, row in before.items() if row[3] == pid}
                assert set(known) == {pid, active['member_pid']}
                assert all(row[1] == os.getuid() for row in known.values())
                assert known[pid][2] == native['process_start']
                assert api['snapshot_belongs_to'](before, pid, runner, os.getuid())
                try:
                    assert rows()[runner] == before[runner]
                    os.kill(runner, signal.SIGKILL)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if not any(row[3] == pid for row in rows().values()):
                            break
                        await asyncio.sleep(.1)
                    else:
                        pytest.fail('active native group survived runner SIGKILL')
                    await asyncio.wait_for(process.wait(), timeout=15)
                    verify_native_ended(client, ROOT, folder, runner_pid=runner)
                    assert not (folder / 'antigravity/native-ended.json').exists()
                    with pytest.raises(B1Error, match='RESERVATION_CONFLICT'):
                        await parent.call('reserve_files', {'paths': [str(work / 'owned.txt')]})
                finally:
                    # Red-test cleanup, only the two birth/UID/group-verified fixture PIDs.
                    for key, original in known.items():
                        if rows().get(key, (None, None, None, None))[1:] == original[1:]:
                            try:
                                os.kill(key, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
    asyncio.run(run())
