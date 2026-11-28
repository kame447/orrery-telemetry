"""Native Antigravity hooks bound to one verified fresh launch."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys


EDIT_TOOLS = {
    'write_to_file': ('Write', 'TargetFile'),
    'replace_file_content': ('Edit', 'TargetFile'),
    'multi_replace_file_content': ('Edit', 'TargetFile'),
    'write_file': ('Write', 'Path'),
    'edit_file': ('Edit', 'Path'),
}
MAX_INPUT = 1024 * 1024
HANDLER_TIMEOUT = 20


def _reject(*_):
    raise ValueError('invalid hook input')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _edit_target(call):
    from .storage_origin_contract import B1Error

    if not isinstance(call, dict) or not isinstance(call.get('name'), str):
        raise B1Error('FRESH_HOOK_EVENT_INVALID')
    name = call['name']
    if name in {'notebook_edit', 'sed_file'}:
        raise B1Error('FRESH_ANTIGRAVITY_EDITOR_UNSUPPORTED')
    if name not in EDIT_TOOLS:
        raise B1Error('FRESH_HOOK_EVENT_INVALID')
    tool, key = EDIT_TOOLS[name]
    arguments = call.get('args')
    if (not isinstance(arguments, dict)
            or arguments.keys() & {'Path', 'TargetFile'} != {key}):
        raise B1Error('FRESH_HOOK_EVENT_INVALID')
    path = arguments[key]
    if not isinstance(path, str) or not path:
        raise B1Error('INVALID_PATH')
    return tool, path


def _workspace(plan, payload):
    if not isinstance(payload, dict):
        _reject()
    paths = payload.get('workspacePaths')
    if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
        _reject()
    resolved = set()
    for value in paths:
        if (not isinstance(value, str) or not value or len(value) > 4096
                or '\x00' in value or not Path(value).is_absolute()):
            _reject()
        path = Path(value).resolve(strict=True)
        if not path.is_dir():
            _reject()
        resolved.add(path)
    cwd = Path(plan['cwd']).resolve(strict=True)
    integration = Path(plan['integration_dir']).resolve(strict=True)
    if Path.cwd().resolve(strict=True) != cwd or not {cwd, integration} <= resolved:
        _reject()


async def handle(plan_path, event, payload):
    # Import inside the protected handler: dependency errors must deny too.
    from .fresh_antigravity import (hook_evidence, hook_origin, native_session_id,
                                   read_hook_plan, same_hook_policy)
    from .fresh_end import present
    from .fresh_launch import same_session_file
    from .fresh_proxy import verified_owner
    from .fresh_runtime import _directory, _read_private
    from .storage_origin_contract import B1Error, canonical

    if event not in {'session', 'invocation', 'guard'} or not isinstance(payload, dict):
        _reject()
    session_id = native_session_id(payload.get('conversationId'))
    client, plan, run, intent, source = read_hook_plan(plan_path, session_id)
    _workspace(plan, payload)
    if source != plan:
        _workspace(source, payload)
    await verified_owner(client)
    checked, current_plan, current_run, current_intent, current_source = read_hook_plan(plan_path, session_id)
    if (checked.path != client.path or checked.context_digest != client.context_digest
            or current_plan != plan or current_run != run or current_intent != intent
            or current_source != source):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    evidence = hook_evidence(client, plan, session_id)
    directory = run / 'antigravity'
    _directory(directory)
    path = directory / 'hook-session.json'
    origin_path = directory / 'hook-origin.json'

    def current_proof(create_event=None):
        if present(origin_path):
            origin = _read_private(origin_path)
            if origin != hook_origin(client, plan, session_id, origin.get('event')):
                raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
        elif present(path):
            raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
        elif create_event is not None:
            same_session_file(origin_path, hook_origin(client, plan, session_id, create_event))
        else:
            return False
        if create_event is not None:
            same_session_file(path, evidence)
        if not present(path):
            raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
        if canonical(_read_private(path)) != canonical(evidence):
            raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
        return True

    if source['launch_id'] != run.name:
        if event == 'session':
            return {}
        if event == 'invocation':
            if not current_proof() and not same_hook_policy(source, plan):
                raise B1Error('FRESH_ANTIGRAVITY_CONFIG_CHANGED')
            return {}
        confirmed = current_proof()
    else:
        observed = ('SessionStart' if event == 'session' else
                    'PreInvocation' if event == 'invocation' and intent['resume_session_id'] is not None else None)
        confirmed = current_proof(observed)
    if not confirmed:
        raise B1Error('FRESH_NATIVE_SESSION_MISMATCH')
    if event == 'session':
        text = (f"Use the {plan['mcp_name']} MCP server for ORRERY fresh Mail. "
                'Use that MCP server, not the native send_message tool. '
                'The public arguments for send_message are recipients (a list of '
                'objects such as {"target_agent_id": 123}), subject and body_md; '
                'do not use to or body. Use the actual recipient ID, not 123. '
                'Use fetch_inbox with {}, mark_message_read and acknowledge_message '
                'with {"message_id": <actual integer ID>}, and reply_message with '
                'message_id and body_md for received instructions. '
                'Keep the bound identity and existing file reservations.')
        return {'injectSteps': [{'systemMessage': {'systemMessage': text}}]}
    if event == 'invocation':
        return {}
    tool, path = _edit_target(payload.get('toolCall'))
    from .fresh_guard import check
    await check(str(client.path), {
        'tool_name': tool, 'cwd': plan['cwd'],
        'tool_input': {'file_path': path},
    })
    return {'decision': 'allow'}


async def _bounded_handle(plan_path, event, payload):
    return await asyncio.wait_for(handle(plan_path, event, payload), HANDLER_TIMEOUT)


def failure(event, error):
    code = getattr(error, 'code', '')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', code):
        code = 'FRESH_ANTIGRAVITY_HOOK_REJECTED'
    reason = 'ORRERY fresh: ' + code
    if event == 'guard':
        return {'decision': 'deny', 'reason': reason}
    return {'terminationBehavior': 'terminate',
            'injectSteps': [{'errorMessage': {'userMessage': reason}}]}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _reject()


def main(argv=None):
    event = 'guard'
    try:
        parser = _Parser(description=__doc__, add_help=False)
        parser.add_argument('--plan', required=True)
        parser.add_argument('--event', choices=('session', 'invocation', 'guard'), required=True)
        options = parser.parse_args(argv)
        event = options.event
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw.encode('utf-8')) > MAX_INPUT:
            _reject()
        payload = json.loads(raw, object_pairs_hook=_object, parse_constant=_reject)
        result = asyncio.run(_bounded_handle(options.plan, event, payload))
    except (Exception, KeyboardInterrupt) as error:
        result = failure(event, error)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
