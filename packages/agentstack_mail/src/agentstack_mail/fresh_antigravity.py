"""Antigravity's native stream entry, bound to one fresh owner and launch.

The integration workspace contains only hook/MCP definitions. The user's
profile, login, permissions and working-directory configuration stay native.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import sys
from uuid import UUID

from .fresh_runtime import _directory, _read_private, _write_private
from .storage_origin_contract import B1Error, digest

EFFORTS = {'low', 'medium', 'high', 'xhigh', 'max'}


def native_session_id(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise B1Error('FRESH_NATIVE_SESSION_INVALID') from None
    return value


def setup(client, root, *, owner=None):
    from .fresh_child import family, provider_home

    _, _, ready = family(client)
    if ready is not None and ready['program'] != 'antigravity':
        raise B1Error('FRESH_CHILD_INTENT_CONFLICT')
    explicit = os.environ.get('AGENTSTACK_GEMINI_BIN')
    search_path = os.pathsep.join([os.environ.get('PATH', ''), str(Path.home() / '.local/bin'),
                                  '/opt/homebrew/bin', '/usr/local/bin'])
    binary = shutil.which(explicit if explicit is not None else 'agy', path=search_path)
    if binary is None:
        raise B1Error('FRESH_PROVIDER_UNAVAILABLE')
    binary = str(Path(binary).resolve(strict=True))
    model = ready['model'] if ready is not None else (owner or {}).get('model', os.environ.get('AGENTSTACK_GEMINI_MODEL'))
    effort = (ready.get('effort') if ready is not None else None) or os.environ.get('AGENTSTACK_GEMINI_EFFORT', 'high')
    if (not isinstance(model, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', model) is None
            or effort not in EFFORTS):
        raise B1Error('FRESH_ANTIGRAVITY_SELECTION_INVALID')
    home = provider_home(client, 'antigravity')
    return home, binary, {'binary': binary, 'model': model, 'effort': effort}


def plan(client, root, settings, run, cwd, prompt, session_id):
    from .fresh_launch import identity

    if session_id is not None:
        native_session_id(session_id)
    state = run / 'antigravity'
    integration = state / 'integration'
    for folder in (state, integration, integration / '.agents'):
        folder.mkdir(mode=0o700)
        _directory(folder)
    plan_path = state / 'plan.json'
    # Native mcp_<server>_<tool> names must fit the 64-character bound.
    mcp_name = 'orrery-' + UUID(client.context['client_id']).hex
    source = Path(__file__).resolve().parents[1]
    python_path = os.pathsep.join([str(source), str(root)])
    shared_env = {'AGENTSTACK_RUNTIME_MODE': 'fresh',
                  'AGENTSTACK_FRESH_CLIENT_CONTEXT': str(client.path),
                  'AGENTSTACK_PYTHON': sys.executable, 'PYTHONPATH': python_path}
    mcp = {'mcpServers': {mcp_name: {
        'command': sys.executable,
        'args': ['-m', 'agentstack_mail.fresh_client', 'proxy', '--context', str(client.path)],
        'env': shared_env, 'cwd': str(cwd)}}}
    _write_private(integration / '.agents/mcp_config.json', mcp)

    def handler(event):
        command = shlex.join(['cd', str(cwd)]) + ' && ' + shlex.join([
            sys.executable, '-m', 'agentstack_mail.fresh_antigravity_hooks',
            '--plan', str(plan_path), '--event', event])
        return {'type': 'command', 'command': command, 'timeout': 30}

    hooks = {'orrery-fresh-' + run.name: {
        'SessionStart': [handler('session')],
        'PreInvocation': [handler('invocation')],
        'PreToolUse': [{'matcher': 'write_to_file|replace_file_content|multi_replace_file_content|write_file|edit_file|notebook_edit|sed_file',
                        'hooks': [handler('guard')]}]}}
    _write_private(integration / '.agents/hooks.json', hooks)
    argv = [settings['binary'], '--model', settings['model'], '--effort', settings['effort'],
            '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--add-dir', str(integration)]
    if session_id is not None:
        argv += ['--conversation', session_id]
    configs = {str(path.relative_to(state)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in (integration / '.agents/mcp_config.json', integration / '.agents/hooks.json')}
    document = {'kind': 'orrery-fresh-antigravity-plan-v1', 'launch_id': run.name,
                'context_path': str(client.path), 'context_digest': client.context_digest,
                'wrapper_root': str(root), 'cwd': str(cwd), 'run_dir': str(run),
                'integration_dir': str(integration), 'mcp_name': mcp_name,
                'resume_session_id': session_id, 'argv': argv, 'prompt': prompt,
                'config_hashes': configs, **identity(client)}
    _write_private(plan_path, document)
    return {'argv': [sys.executable, '-m', 'agentstack_mail.fresh_antigravity_runner',
                     '--plan', str(plan_path)],
            'env': {**shared_env, 'AGENTSTACK_ANTIGRAVITY_PLAN_DIGEST': digest(document)}}


def _plan_record(client, run):
    from .fresh_end import run_directory, validate_intent
    from .fresh_launch import pinned

    run_directory(client, run)
    state = run / 'antigravity'
    _directory(state)
    row = _read_private(state / 'plan.json', max_bytes=1024 * 1024)
    intent = _read_private(run / 'intent.json')
    validate_intent(client, run, intent)
    for value in (row, intent):
        pinned(client, value)
    if (row.get('kind') != 'orrery-fresh-antigravity-plan-v1'
            or row.get('launch_id') != run.name or intent.get('launch_id') != run.name
            or row.get('run_dir') != str(run) or row.get('context_path') != str(client.path)
            or row.get('integration_dir') != str(state / 'integration')
            or row.get('wrapper_root') != intent.get('wrapper_root')
            or row.get('cwd') != intent.get('cwd') or intent.get('provider') != 'antigravity'
            or row.get('resume_session_id') != intent.get('resume_session_id')):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    for directory in (state / 'integration', state / 'integration/.agents'):
        _directory(directory)
    expected = {'integration/.agents/mcp_config.json', 'integration/.agents/hooks.json'}
    if not isinstance(row.get('config_hashes'), dict) or set(row['config_hashes']) != expected:
        raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
    for relative, value in row['config_hashes'].items():
        path = state / relative
        _read_private(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != value:
            raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
    return row, intent


def _bound_plan(client, run):
    from .fresh_end import read_run_end

    row, intent = _plan_record(client, run)
    if (_read_private(run.parent.parent / 'current.json') != {'launch_id': run.name}
            or read_run_end(client, run) is not None):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    return row, intent


def read_plan(plan_path):
    from .fresh_client import FreshBoundClient
    from .fresh_launch import layout

    if (os.environ.get('AGENTSTACK_RUNTIME_MODE') != 'fresh'
            or not os.environ.get('AGENTSTACK_FRESH_CLIENT_CONTEXT')
            or os.environ.get('AGENTSTACK_CLIENT_CONFIG')
            or os.environ.get('AGENTSTACK_GLOBAL_SERVER_CONFIG')):
        raise B1Error('FRESH_CONTEXT_REQUIRED')
    client = FreshBoundClient(os.environ['AGENTSTACK_FRESH_CLIENT_CONTEXT'])
    run = layout(client) / 'runs' / native_session_id(os.environ.get('AGENTSTACK_FRESH_LAUNCH_ID'))
    if Path(plan_path) != run / 'antigravity/plan.json':
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    row, intent = _bound_plan(client, run)
    if (intent.get('nonce') != os.environ.get('AGENTSTACK_FRESH_LAUNCH_NONCE')
            or digest(row) != os.environ.get('AGENTSTACK_ANTIGRAVITY_PLAN_DIGEST')):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    return client, row, run, intent


def _session_index(client, session_id):
    from .fresh_launch import identity

    base = client.path.parent
    reference = base / 'providers/antigravity/sessions' / (session_id + '.json')
    for directory in (base / 'launch/sessions', base / 'providers',
                      reference.parent.parent, reference.parent):
        _directory(directory)
    binding = dict(context_digest=client.context_digest, **identity(client))
    receipt = dict(kind='orrery-fresh-native-session-v1', provider='antigravity',
                   native_session_id=session_id, transcript_path=str(reference), **binding)
    if (_read_private(base / 'launch/sessions' / (session_id + '.json')) != receipt
            or _read_private(reference) != dict(harness='antigravity',
                native_session_id=session_id, **binding)):
        raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    return receipt


def hook_evidence(client, plan, session_id):
    from .fresh_launch import identity

    return dict(native_session_id=session_id, launch_id=plan['launch_id'],
                context_digest=client.context_digest, **identity(client))


def hook_origin(client, plan, session_id, event):
    if (event not in {'SessionStart', 'PreInvocation'}
            or event == 'PreInvocation' and plan.get('resume_session_id') != session_id):
        raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    return dict(hook_evidence(client, plan, session_id), event=event,
                source_plan_path=str(Path(plan['run_dir']) / 'antigravity/plan.json'),
                source_plan_digest=digest(plan))


def _historical_plan(client, run, current, session_id, receipt):
    from .fresh_antigravity_end import _observation, _record
    from .fresh_end import present, read_run_end
    from .fresh_launch import identity, pinned

    plan, intent = _plan_record(client, run)
    if (plan['cwd'] != current['cwd'] or plan['wrapper_root'] != current['wrapper_root']
            or intent['resume_session_id'] not in (None, session_id)):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    ended = read_run_end(client, run)
    if ended is None:
        raise B1Error('FRESH_LAUNCH_OUTCOME_UNKNOWN')
    state = run / 'antigravity'
    process = _read_private(state / 'native-process.json')
    _, record, native = _record(client, plan['wrapper_root'], run,
        runner_pid=process.get('runner_pid'), require_current=False)
    if not native:
        raise B1Error('FRESH_LAUNCH_OUTCOME_UNKNOWN')
    if ended.kind == 'normal':
        proof = _read_private(state / 'native-ended.json')
        if proof != dict(launch_id=run.name, context_digest=client.context_digest,
                         observation=_observation(record, True), **identity(client)):
            raise B1Error('FRESH_LAUNCH_OUTCOME_UNKNOWN')
    evidence = hook_evidence(client, plan, session_id)
    expected = {
        state / 'hook-session.json': evidence,
        state / 'native-session.json': dict(evidence, harness='antigravity',
            native_pid=record['pid'], runner_pid=record['runner_pid']),
        run / 'session.json': dict(receipt, launch_id=run.name),
    }
    # A failed resume may leave a discovered integration, but never ready proof.
    for path, value in expected.items():
        if intent['resume_session_id'] is None or present(path):
            actual = _read_private(path)
            pinned(client, actual)
            if actual != value:
                raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    origin = state / 'hook-origin.json'
    if present(origin):
        value = _read_private(origin)
        if value != hook_origin(client, plan, session_id, value.get('event')):
            raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    return plan


def read_hook_plan(plan_path, session_id):
    """Resolve persisted hook configuration without redirecting runner authority."""
    from .fresh_client import FreshBoundClient
    from .fresh_launch import layout

    native_session_id(session_id)
    context = os.environ.get('AGENTSTACK_FRESH_CLIENT_CONTEXT')
    if not context:
        raise B1Error('FRESH_CONTEXT_REQUIRED')
    client = FreshBoundClient(context)
    run = layout(client) / 'runs' / native_session_id(os.environ.get('AGENTSTACK_FRESH_LAUNCH_ID'))
    client, plan, run, intent = read_plan(run / 'antigravity/plan.json')
    resume = intent['resume_session_id']
    if resume is not None and resume != session_id:
        raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    receipt = _session_index(client, session_id) if resume is not None else None
    path = Path(plan_path)
    if path == run / 'antigravity/plan.json':
        return client, plan, run, intent, plan
    source_run = path.parent.parent
    if (resume is None or path != source_run / 'antigravity/plan.json'
            or source_run.parent != run.parent):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    native_session_id(source_run.name)
    source = _historical_plan(client, source_run, plan, session_id, receipt)
    return client, plan, run, intent, source


def same_hook_policy(source, current):
    """Normalize only generated group IDs and the generated --plan argument."""
    def policy(plan):
        directory = Path(plan['integration_dir']) / '.agents'
        groups = _read_private(directory / 'hooks.json')
        name = 'orrery-fresh-' + plan['launch_id']
        if set(groups) != {name} or not isinstance(groups[name], dict):
            raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
        group = groups[name]
        if set(group) != {'SessionStart', 'PreInvocation', 'PreToolUse'}:
            raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')

        def handlers(values, event):
            if not isinstance(values, list) or not values:
                raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
            result = []
            for handler in values:
                if (not isinstance(handler, dict) or set(handler) != {'type', 'command', 'timeout'}
                        or handler['type'] != 'command' or not isinstance(handler['command'], str)):
                    raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
                args = shlex.split(handler['command'])
                if (len(args) != 10 or args[:3] != ['cd', plan['cwd'], '&&']
                        or not Path(args[3]).is_absolute()
                        or args[4:7] != ['-m', 'agentstack_mail.fresh_antigravity_hooks', '--plan']
                        or args[7] != str(Path(plan['run_dir']) / 'antigravity/plan.json')
                        or args[8:] != ['--event', event]
                        or handler['command'] != shlex.join(args[:2]) + ' && ' + shlex.join(args[3:])):
                    raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
                result.append(dict(handler, command=[*args[:7], '<current-plan>', *args[8:]]))
            return result

        tools = group['PreToolUse']
        if not isinstance(tools, list) or not tools:
            raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
        normalized = []
        for entry in tools:
            if (not isinstance(entry, dict) or set(entry) != {'matcher', 'hooks'}
                    or not isinstance(entry['matcher'], str)):
                raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
            normalized.append(dict(entry, hooks=handlers(entry['hooks'], 'guard')))
        return {'hooks': {'SessionStart': handlers(group['SessionStart'], 'session'),
                         'PreInvocation': handlers(group['PreInvocation'], 'invocation'),
                         'PreToolUse': normalized},
                'mcp': _read_private(directory / 'mcp_config.json')}

    return digest(policy(source)) == digest(policy(current))


def session_reference(client, intent, session_id):
    from .fresh_launch import identity, same_session_file

    native_session_id(session_id)
    run = client.path.parent / 'launch/runs' / native_session_id(intent['launch_id'])
    plan, checked = _bound_plan(client, run)
    if checked != intent:
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    path = run / 'antigravity/native-session.json'
    value = _read_private(path)
    evidence = _read_private(run / 'antigravity/hook-session.json')
    origin = _read_private(run / 'antigravity/hook-origin.json')
    expected = {'native_session_id': session_id, 'launch_id': run.name,
                'context_digest': client.context_digest, **identity(client)}
    if (evidence != expected or origin != hook_origin(client, plan, session_id, origin.get('event'))
            or any(value.get(k) != v for k, v in expected.items())
            or value.get('harness') != 'antigravity'
            or type(value.get('native_pid')) is not int or value['native_pid'] < 1
            or type(value.get('runner_pid')) is not int or value['runner_pid'] < 1):
        raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    base = client.path.parent / 'providers'
    for folder in (base, base / 'antigravity', base / 'antigravity/sessions'):
        folder.mkdir(mode=0o700, exist_ok=True)
        _directory(folder)
    reference = base / 'antigravity/sessions' / (session_id + '.json')
    same_session_file(reference, {'harness': 'antigravity', 'native_session_id': session_id,
                                 'context_digest': client.context_digest, **identity(client)})
    return reference


def _socket_identity(row):
    path = Path(row['path'])
    _directory(path.parent)
    info = path.lstat()
    if (not path.is_absolute() or not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077
            or [info.st_dev, info.st_ino] != [row.get('device'), row.get('inode')]):
        raise B1Error('FRESH_ANTIGRAVITY_SOCKET_CHANGED')
    return path


def _exchange(row, request):
    path = _socket_identity(row)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(path))
        _socket_identity(row)
        connection.sendall(json.dumps(request).encode() + b'\n')
        answer = b''
        while b'\n' not in answer and len(answer) <= 4096:
            chunk = connection.recv(4096)
            if not chunk:
                break
            answer += chunk
    if len(answer) > 4096:
        raise B1Error('FRESH_ANTIGRAVITY_DELIVERY_UNKNOWN')
    try:
        value = json.loads(answer)
    except (ValueError, UnicodeError):
        raise B1Error('FRESH_ANTIGRAVITY_DELIVERY_UNKNOWN') from None
    if not isinstance(value, dict) or set(value) != {'accepted'} or type(value['accepted']) is not bool:
        raise B1Error('FRESH_ANTIGRAVITY_DELIVERY_UNKNOWN')
    return value['accepted']


async def deliver(client, root):
    from .fresh_delivery import target
    from .fresh_launch import pinned
    from .fresh_proxy import verified_owner

    await verified_owner(client)
    _, route = target(client, root, readiness=False)
    run = client.path.parent / 'launch/runs' / native_session_id(route['launch_id'])
    _, intent = _bound_plan(client, run)
    socket_row = _read_private(run / 'antigravity/socket.json')
    pinned(client, socket_row)
    if (socket_row.get('launch_id') != run.name
            or socket_row.get('runner_pid') != route['provider']['pid']):
        raise B1Error('FRESH_ANTIGRAVITY_SOCKET_CHANGED')
    target(client, root, route, readiness=False)
    try:
        accepted = await asyncio.to_thread(_exchange, socket_row,
                                          {'launch_id': run.name, 'nonce': intent['nonce']})
    except (OSError, TimeoutError):
        raise B1Error('FRESH_ANTIGRAVITY_DELIVERY_UNKNOWN') from None
    target(client, root, route, readiness=False)
    return accepted
