"""Prove the native agy process group ended before releasing its resources."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import runpy
import time

from .fresh_runtime import _directory, _read_private, _write_private
from .storage_origin_contract import B1Error, digest

KIND = 'orrery-fresh-antigravity-ended-v1'


def process_api(wrapper_root):
    return runpy.run_path(str(Path(wrapper_root) / 'bin/lib/process_observation.py'))


def _record(client, wrapper_root, run, *, runner_pid=None, require_current=True):
    from .fresh_end import present, run_directory, validate_intent
    from .fresh_launch import identity, pinned, root_path

    if require_current:
        root = root_path(wrapper_root)
    else:
        root = Path(wrapper_root)
        if not root.is_absolute() or str(root) != str(wrapper_root):
            raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    run_directory(client, run)
    intent = _read_private(run / 'intent.json')
    validate_intent(client, run, intent)
    if (intent['provider'] != 'antigravity' or intent.get('wrapper_root') != str(root)
            or require_current and _read_private(run.parent.parent / 'current.json') != {'launch_id': run.name}):
        raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    state = run / 'antigravity';_directory(state)
    # A group-empty observation is insufficient after a proven escape.
    if present(state / 'native-containment-lost.json'):
        raise B1Error('FRESH_ANTIGRAVITY_CONTAINMENT_LOST')
    native, absent = (present(state / name) for name in ('native-process.json', 'native-not-started.json'))
    if native == absent:
        raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    value = _read_private(state / ('native-process.json' if native else 'native-not-started.json'))
    pinned(client, value)
    keys = {'launch_id', 'context_digest', *identity(client)}
    if value.get('launch_id') != run.name:
        raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    if not native:
        if (set(value) != keys or any(present(path) for path in (
                state / 'native-session.json', state / 'hook-session.json', run / 'session.json'))):
            raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
        return root, value, False
    keys |= {'pid', 'process_start', 'pgid', 'runner_pid'}
    if (set(value) != keys or any(type(value.get(key)) is not int or value[key] < 1
                               for key in ('pid', 'pgid', 'runner_pid'))
            or value['pid'] != value['pgid'] or value['pid'] == value['runner_pid']
            or not isinstance(value.get('process_start'), str)
            or not 0 < len(value['process_start']) <= 128 or value['process_start'] == 'absent'):
        raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    expected_runner = runner_pid
    if present(run / 'tmux.json'):
        route = _read_private(run / 'tmux.json');pinned(client, route)
        provider = route.get('provider')
        if (route.get('launch_id') != run.name or not isinstance(provider, dict)
                or type(provider.get('pid')) is not int or provider['pid'] < 1
                or expected_runner is not None and expected_runner != provider['pid']):
            raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
        expected_runner = provider['pid']
    if (type(expected_runner) is not int or expected_runner < 1
            or expected_runner != value['runner_pid']):
        raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    if present(state / 'native-session.json'):
        session = _read_private(state / 'native-session.json');pinned(client, session)
        if (session.get('launch_id') != run.name or session.get('harness') != 'antigravity'
                or session.get('native_pid') != value['pid']
                or session.get('runner_pid') != value['runner_pid']):
            raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    return root, value, True


def cleanup_receipt(record):
    """Positive, launch-bound evidence from the direct native owner."""
    return dict(kind='orrery-fresh-antigravity-owner-cleanup-v1',
                record_digest=digest(record))


def _require_cleanup(state, record):
    try:
        if _read_private(state / 'native-cleanup.json') == cleanup_receipt(record):
            return
    except (B1Error, OSError, ValueError, TypeError):
        pass
    raise B1Error('FRESH_ANTIGRAVITY_CLEANUP_UNCONFIRMED')


def _observation(record, native):
    result = {'kind': KIND, 'record_digest': digest(record)}
    if not native:
        return dict(result, state='not-started')
    return dict(result,
        process={'pid': record['pid'], 'process_start': record['process_start'], 'state': 'absent'},
        group={'pgid': record['pgid'], 'state': 'empty'})


def valid_native_observation(client, wrapper_root, run, value):
    """Validate historical proof without treating later PID reuse as a live run."""
    try:
        _, record, native = _record(client, wrapper_root, run, require_current=False)
        if native:
            _require_cleanup(run / 'antigravity', record)
        return value == _observation(record, native)
    except (B1Error, OSError, ValueError, KeyError, TypeError):
        return False


def valid_normal_native_end(client, wrapper_root, run):
    """Revalidate immutable owner evidence, not the current OS PID state."""
    from .fresh_end import present

    try:
        state = run / 'antigravity'
        runner_pid = None
        if present(state / 'native-process.json'):
            runner_pid = _read_private(state / 'native-process.json').get('runner_pid')
        _, record, native = _record(client, wrapper_root, run,
                                    runner_pid=runner_pid, require_current=False)
        if native:
            _require_cleanup(state, record)
        return True
    except (B1Error, OSError, ValueError, KeyError, TypeError):
        return False


def verify_native_ended(client, wrapper_root, run, *, runner_pid=None, deadline=None):
    root, record, native = _record(client, wrapper_root, run, runner_pid=runner_pid)
    if not native:
        return _observation(record, False)
    deadline = time.monotonic() + 10 if deadline is None else deadline
    api = process_api(root)
    for _ in range(2):
        if time.monotonic() >= deadline:
            raise B1Error('FRESH_ANTIGRAVITY_END_OBSERVATION_UNAVAILABLE')
        try:
            actual = api['process_start'](record['pid'], deadline=deadline)
            rows = api['process_snapshot'](include_groups=True, deadline=deadline)
        except (OSError, RuntimeError):
            raise B1Error('FRESH_ANTIGRAVITY_END_OBSERVATION_UNAVAILABLE') from None
        if (actual is None or not isinstance(rows, dict) or not rows
                or any(type(pid) is not int or pid < 1
                    or not isinstance(row, (tuple, list)) or len(row) != 4
                    or any(type(row[i]) is not int or row[i] < 0 for i in (0, 1, 3))
                    or not isinstance(row[2], str) or not row[2]
                    for pid, row in rows.items())):
            raise B1Error('FRESH_ANTIGRAVITY_END_OBSERVATION_UNAVAILABLE')
        if record['pid'] in rows or actual != 'absent':
            same = (actual == record['process_start'] and record['pid'] in rows
                    and rows[record['pid']][1:3] == (os.getuid(), record['process_start']))
            raise B1Error('FRESH_ANTIGRAVITY_NATIVE_ALIVE' if same else 'FRESH_ANTIGRAVITY_NATIVE_CHANGED')
        if any(row[3] == record['pgid'] for row in rows.values()):
            raise B1Error('FRESH_ANTIGRAVITY_GROUP_ALIVE')
        if _record(client, root, run, runner_pid=runner_pid)[1:] != (record, native):
            raise B1Error('FRESH_ANTIGRAVITY_END_RECORD_INVALID')
    # Absence of the recorded group is not evidence of owner cleanup: both
    # monitors may have died while a detached descendant remained alive.
    _require_cleanup(run / 'antigravity', record)
    return _observation(record, True)


def release_ready(client, run, intent, task, observation):
    from .fresh_launch import identity

    return dict(kind='orrery-fresh-antigravity-release-ready-v1',
                launch_id=run.name, context_digest=client.context_digest,
                intent_digest=digest(intent), task_digest=digest(task),
                observation_digest=digest(observation), **identity(client))


def finalize_native_launch(client, root, run, *, runner_pid, code, intent, task):
    """Publish durable closure before making a child reservation reusable.

    A missing runner release receipt means retain; do not infer eligibility.
    A failed Mail release after closure is not automatically retried: a later
    reservation may already have been created for the same path.
    """
    from .fresh_child import launch_task
    from .fresh_end import present
    from .fresh_launch import identity

    try:
        observation = verify_native_ended(client, root, run, runner_pid=runner_pid)
        state = run / 'antigravity'
        receipt = state / 'release-ready.json'
        resources = []
        if present(receipt):
            current_task, _ = launch_task(client)
            if (task is None or current_task != task
                    or task.get('spawn_id') != intent.get('child_spawn_id')
                    or not task.get('resources')
                    or intent.get('resume_session_id') is not None
                       and observation.get('state') == 'not-started'
                    or _read_private(state / 'native-ended.json') != dict(
                        launch_id=run.name, context_digest=client.context_digest,
                        **identity(client), observation=observation)
                    or _read_private(receipt) != release_ready(client, run, intent, task, observation)):
                raise B1Error('FRESH_ANTIGRAVITY_RELEASE_BINDING_MISMATCH')
            resources = task['resources']
    except (B1Error, OSError, ValueError, KeyError, TypeError):
        raise B1Error('FRESH_LAUNCH_OUTCOME_UNKNOWN', launch_id=run.name) from None
    _write_private(run / 'ended.json', {'launch_id': run.name, 'exit_code': code})
    if resources:
        asyncio.run(client.call('release_reservations', {'paths': resources}))
