"""Native Antigravity hooks bound to one verified fresh launch."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys


EDIT_TOOLS = {
    'write_to_file': 'Write',
    'replace_file_content': 'Edit',
    'multi_replace_file_content': 'Edit',
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
    from .fresh_antigravity import native_session_id, read_plan
    from .fresh_launch import identity, same_session_file
    from .fresh_proxy import verified_owner
    from .fresh_runtime import _directory, _read_private
    from .storage_origin_contract import B1Error, canonical

    if event not in {'session', 'invocation', 'guard'}:
        _reject()
    client, plan, run, intent = read_plan(plan_path)
    _workspace(plan, payload)
    session_id = native_session_id(payload.get('conversationId'))
    await verified_owner(client)
    checked, current_plan, current_run, current_intent = read_plan(plan_path)
    if (checked.path != client.path or checked.context_digest != client.context_digest
            or current_plan != plan or current_run != run or current_intent != intent):
        raise B1Error('FRESH_LAUNCH_RECEIPT_MISMATCH')
    evidence = dict(native_session_id=session_id, launch_id=intent['launch_id'],
                    context_digest=client.context_digest, **identity(client))
    directory = run / 'antigravity'
    _directory(directory)
    path = directory / 'hook-session.json'
    if event == 'session':
        same_session_file(path, evidence)
    if canonical(_read_private(path)) != canonical(evidence):
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
    call = payload.get('toolCall')
    if isinstance(call, dict) and call.get('name') in {'notebook_edit', 'sed_file'}:
        raise B1Error('FRESH_ANTIGRAVITY_EDITOR_UNSUPPORTED')
    if not isinstance(call, dict) or call.get('name') not in EDIT_TOOLS:
        raise B1Error('FRESH_HOOK_EVENT_INVALID')
    arguments = call.get('args')
    if not isinstance(arguments, dict):
        raise B1Error('FRESH_HOOK_EVENT_INVALID')
    from .fresh_guard import check
    await check(str(client.path), {
        'tool_name': EDIT_TOOLS[call['name']], 'cwd': plan['cwd'],
        'tool_input': {'file_path': arguments.get('TargetFile')},
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
