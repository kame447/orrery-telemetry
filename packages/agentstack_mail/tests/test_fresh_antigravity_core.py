"""Fresh Antigravity host boundaries; synthetic native process, real Mail HTTP."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shlex
import subprocess
from uuid import uuid4

import pytest

from agentstack_mail import fresh_antigravity as agy
from agentstack_mail import fresh_antigravity_end as native_end
from agentstack_mail import fresh_antigravity_hooks as agy_hooks
from agentstack_mail import fresh_child as child_api
from agentstack_mail import fresh_delivery as delivery
from agentstack_mail import fresh_launch as launch_api
from agentstack_mail.fresh_client import FreshBoundClient
from agentstack_mail.fresh_proxy import PUBLISHED
from agentstack_mail.fresh_runtime import _read_private, _write_private
from agentstack_mail.storage_origin_contract import B1Error, canonical
from test_fresh_delivery import mocked_os, prepared_route
from test_fresh_guard import live
from test_fresh_mail import snapshot

ROOT = Path(__file__).resolve().parents[3]


class SyntheticTransport:
    identity = (1, 1)

    def __init__(self, path):
        self.path = path
        self.calls = []

    def command(self, *args):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, '', '')


async def prepared(parent, effort='high'):
    row = await child_api.prepare(parent, 'agy-worker', 'AgyWorker', 'antigravity',
        'gemini-4-neon-high', 'bounded native fixture', effort=effort)
    return FreshBoundClient(row['context_path'])


def test_agy_child_registration_preserves_fresh_owner_and_replays(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTSTACK_PROJECT_KEY', '/unrelated-legacy-project')
    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            child = await prepared(parent, 'max')
            again = await prepared(parent, 'max')
            assert child.path == again.path
            assert child.context['mail_instance_id'] == parent.context['mail_instance_id']
            assert child.context['mail_agent_id'] != parent.context['mail_agent_id']
            owner, _, ready = child_api.family(child)
            assert owner.path == parent.path
            assert ready['program'] == 'antigravity' and ready['effort'] == 'max'
            assert (await child.call('reconnect', {}))['mail_agent_id'] == child.context['mail_agent_id']
            with pytest.raises(B1Error, match='FRESH_CHILD_INTENT_CONFLICT'):
                await prepared(parent, 'low')
    asyncio.run(run())


def test_agy_invalid_effort_rejects_before_registration(tmp_path):
    async def run():
        async with live(tmp_path) as (runtime, _, parent, _):
            before = await snapshot(runtime)
            for effort in ('ultra', 'auto', False, ''):
                with pytest.raises(B1Error, match='FRESH_CHILD_ARGUMENTS_INVALID'):
                    await prepared(parent, effort)
                assert await snapshot(runtime) == before
            assert not (parent.path.parent / 'children').exists()
    asyncio.run(run())


def test_agy_handoff_pins_binary_and_keeps_task_out_of_shell(tmp_path, monkeypatch):
    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            child = await prepared(parent)
            binary = str(tmp_path / 'fake agy')
            monkeypatch.setattr(child_api, 'provider_setup', lambda *a, **k: (tmp_path, binary, {}))
            task = 'Read literally: $(touch unwanted) and `touch unwanted2`'
            transport = SyntheticTransport(tmp_path / 'private.sock')
            first = await child_api.handoff(parent, ROOT, slot='agy-worker', cwd=tmp_path,
                task=task, resources=['report.txt'], transport=transport)
            again = await child_api.handoff(parent, ROOT, slot='agy-worker', cwd=tmp_path,
                task=task, resources=['report.txt'], transport=transport)
            assert first == again and len(transport.calls) == 1
            argv = shlex.split(transport.calls[0][-1])
            assert 'AGENTSTACK_GEMINI_BIN=' + binary in argv
            assert 'AGENTSTACK_FRESH_CLIENT_CONTEXT=' + str(child.path) in argv
            assert not any(x.startswith(('AGENTSTACK_ANTIGRAVITY_BIN=', 'AGENTSTACK_PROJECT_KEY=',
                                         'CHILD_REGISTRATION_TOKEN=')) for x in argv)
            assert task not in transport.calls[0][-1]
            assert argv[argv.index('child-run') - 3:argv.index('child-run')] == [
                '-P', '-m', 'agentstack_mail.fresh_client']
            shadow = tmp_path / 'agentstack_mail'
            shadow.mkdir()
            (shadow / '__init__.py').write_text('raise SystemExit("WORKSPACE_SHADOW_EXECUTED")')
            # The exact generated env command, but invoke only harmless help.
            help_argv = [*argv[:argv.index('child-run')], '--help']
            probe = subprocess.run(help_argv, cwd=tmp_path, env={**os.environ, 'PYTHONPATH': '.'},
                                   capture_output=True, text=True, timeout=20)
            assert probe.returncode == 0 and 'WORKSPACE_SHADOW_EXECUTED' not in probe.stderr + probe.stdout
            _, base, _ = child_api.family(child)
            assert child_api.read_task(base)['task'] == task
            assert not first['native_ready'] and first['execution'] == 'unknown'
    asyncio.run(run())


def test_agy_native_plan_keeps_all_public_tool_names_within_cli_limit(tmp_path):
    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            child = await prepared(parent)
            folder = tmp_path / str(uuid4())
            folder.mkdir(mode=0o700)
            settings = {'binary': str(tmp_path / 'synthetic-agy'),
                        'model': 'gemini-4-neon-high', 'effort': 'high'}
            launch = agy.plan(child, ROOT, settings, folder, tmp_path, 'bounded native task', None)
            plan = _read_private(folder / 'antigravity/plan.json')
            config = _read_private(folder / 'antigravity/integration/.agents/mcp_config.json')
            assert set(config['mcpServers']) == {plan['mcp_name']}
            groups = _read_private(folder / 'antigravity/integration/.agents/hooks.json')
            proxy = config['mcpServers'][plan['mcp_name']]
            commands = [launch['argv'], [proxy['command'], *proxy['args']],
                        shlex.split(next(iter(groups.values()))['SessionStart'][0]['command'])[3:]]
            shadow = tmp_path / 'agentstack_mail'
            shadow.mkdir()
            (shadow / '__init__.py').write_text('raise SystemExit("WORKSPACE_SHADOW_EXECUTED")')
            for argv in commands:
                assert argv[1:3] == ['-P', '-m']
                probe = subprocess.run([*argv[:4], '--help'], cwd=tmp_path,
                    env={**os.environ, **launch['env']}, capture_output=True, text=True, timeout=20)
                assert 'WORKSPACE_SHADOW_EXECUTED' not in probe.stderr + probe.stdout
                assert probe.returncode == 0, probe.stderr
            matcher = next(iter(groups.values()))['PreToolUse'][0]['matcher']
            for tool in ('write_to_file', 'replace_file_content', 'multi_replace_file_content',
                         'write_file', 'edit_file', 'notebook_edit', 'sed_file'):
                assert re.search(matcher, tool), tool
            assert not re.search(matcher, 'read_file')
            # agy 1.5.16 only enables composed native tool names up to 64 characters.
            for tool in PUBLISHED | {'runtime_status', 'show_files'}:
                name = f"mcp_{plan['mcp_name']}_{tool}"
                assert len(name) <= 64, name
    asyncio.run(run())


@pytest.mark.parametrize(('name', 'tool', 'key', 'other'), [
    ('write_to_file', 'Write', 'TargetFile', 'Path'),
    ('replace_file_content', 'Edit', 'TargetFile', 'Path'),
    ('multi_replace_file_content', 'Edit', 'TargetFile', 'Path'),
    ('write_file', 'Write', 'Path', 'TargetFile'),
    ('edit_file', 'Edit', 'Path', 'TargetFile'),
])
def test_agy_edit_target_requires_tool_specific_selector(name, tool, key, other):
    assert agy_hooks._edit_target({'name': name, 'args': {key: '/reserved.txt'}}) == (
        tool, '/reserved.txt')
    for arguments in ({}, {other: '/reserved.txt'},
                      {key: '/reserved.txt', other: '/unreserved.txt'},
                      {key: '/reserved.txt', other: '/reserved.txt'}):
        with pytest.raises(B1Error, match='FRESH_HOOK_EVENT_INVALID'):
            agy_hooks._edit_target({'name': name, 'args': arguments})
    for value in (None, '', [], 1):
        with pytest.raises(B1Error, match='INVALID_PATH'):
            agy_hooks._edit_target({'name': name, 'args': {key: value}})


@pytest.mark.parametrize('native_ended', [True, False])
def test_agy_launch_uses_native_plan_without_codex_or_claude_options(tmp_path, monkeypatch, native_ended):
    for key in ('TMUX', 'TMUX_PANE', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('CHILD_REGISTRATION_TOKEN', 'legacy-synthetic-token')
    monkeypatch.setenv('AGENTSTACK_PROJECT_KEY', '/legacy-project')
    observed = {}
    async def run():
        async with live(tmp_path) as (_, _, parent, _):
            child = await prepared(parent)
            binary = str(tmp_path / 'synthetic-agy')
            setup = lambda *a, **k: (tmp_path, binary, {'fixture': True})
            monkeypatch.setattr(child_api, 'provider_setup', setup)
            monkeypatch.setattr(launch_api, 'provider_setup', setup)
            await child_api.handoff(parent, ROOT, slot='agy-worker', cwd=tmp_path,
                task='bounded native task', resources=['report.txt'],
                transport=SyntheticTransport(tmp_path / 'private.sock'))
            def plan(client, root, settings, folder, cwd, prompt, session_id):
                observed.update(run=folder, prompt=prompt)
                assert settings == {'fixture': True} and session_id is None
                return {'argv': [binary, '--native-fixture'], 'env': {'AGY_NATIVE_FIXTURE': '1'}}
            monkeypatch.setattr(agy, 'plan', plan)
            class Process:
                pid = 4242
                def __init__(self, args, *, cwd, env):
                    observed.update(argv=args, env=env)
                def wait(self):
                    intent = _read_private(observed['run'] / 'intent.json')
                    _write_private(observed['run'] / 'session.json', dict(
                        context_digest=child.context_digest, **launch_api.identity(child),
                        provider='antigravity', launch_id=intent['launch_id'],
                        native_session_id=str(uuid4()), transcript_path=str(observed['run'] / 'fixture')))
                    return 0
                def poll(self):return 0
            monkeypatch.setattr(launch_api.subprocess, 'Popen', Process)
            def verify(client, root, folder, *, runner_pid):
                assert runner_pid == Process.pid and folder == observed['run']
                if not native_ended:raise B1Error('FRESH_ANTIGRAVITY_GROUP_ALIVE')
                return {'fixture_observation': True}
            monkeypatch.setattr(native_end, 'verify_native_ended', verify)
            if native_ended:
                assert await asyncio.to_thread(launch_api.launch, child, ROOT, 'antigravity', tmp_path) == 0
            else:
                with pytest.raises(B1Error, match='FRESH_LAUNCH_OUTCOME_UNKNOWN'):
                    await asyncio.to_thread(launch_api.launch, child, ROOT, 'antigravity', tmp_path)
            assert observed['argv'] == [binary, '--native-fixture']
            assert 'bounded native task' in observed['prompt']
            assert observed['env']['AGY_NATIVE_FIXTURE'] == '1'
            assert not any(k in observed['env'] for k in (
                'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'CHILD_REGISTRATION_TOKEN', 'AGENTSTACK_PROJECT_KEY'))
            assert (observed['run'] / 'ended.json').exists() is native_ended
            if native_ended:assert _read_private(observed['run'] / 'ended.json')['exit_code'] == 0
    asyncio.run(run())


def test_agy_wakeup_does_not_paste_or_claim_notification_completion(tmp_path, monkeypatch):
    async def run():
        async with live(tmp_path) as (_, _, client, _):
            folder, _ = await prepared_route(client, tmp_path, monkeypatch)
            for path in (folder / 'intent.json', folder / 'session.json'):
                value = _read_private(path);value['provider'] = 'antigravity'
                path.write_text(canonical(value))
            receipt = _read_private(folder / 'session.json')
            saved = client.path.parent / 'launch/sessions' / (receipt['native_session_id'] + '.json')
            value = _read_private(saved);value['provider'] = 'antigravity';saved.write_text(canonical(value))
            commands, _, _ = mocked_os(monkeypatch)
            wakes = []
            async def signal(owner, root):
                delivery.target(owner, root, readiness=False)
                wakes.append(owner.path)
                return True
            monkeypatch.setattr(agy, 'deliver', signal)
            assert await delivery.deliver(client, ROOT) == {'status': 'signalled', 'completed': 0}
            assert wakes == [client.path] and commands == []
            with pytest.raises(B1Error, match='FRESH_ANTIGRAVITY_STREAM_REQUIRED'):
                delivery.target(client, ROOT)
            assert commands == []
    asyncio.run(run())
