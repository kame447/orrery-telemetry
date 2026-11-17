"""Synthetic loopback and launchd/lsof proof tests; no installed processes."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src/orrery_chatgpt_mcp/fresh_process_probe.py'
spec = importlib.util.spec_from_file_location('fresh_process_probe', SOURCE)
import sys
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sys.path.insert(0, str(SOURCE.parents[1]))
from orrery_chatgpt_mcp.model import TOOL_DEFINITIONS as READ_DEFINITIONS
from orrery_chatgpt_mcp.fresh_messages import TOOL_DEFINITIONS as MESSAGE_DEFINITIONS

INSTANCE = '0123456789abcdef0123456789abcdef'
SECRET = 'a' * 43
LABEL = 'org.orrery.test.fresh-mcp'
PLIST = '/synthetic/test/LaunchAgents/org.orrery.test.fresh-mcp.plist'
AUTH = module.Authority(INSTANCE, 11, ((13, 'TargetAlpha', 'to'),))


def status():
    return {
        'dashboard': {'runtime_mode': 'fresh', 'mail_instance_id': INSTANCE},
        'adapter': {'read_only': False, 'spawn_enabled_on_server': False,
                    'message_enabled_on_server': True},
        'message_authority': {
            'mail_instance_id': INSTANCE, 'sender_agent_id': 11,
            'recipients': [{'target_agent_id': 13, 'expected_target_name': 'TargetAlpha', 'kind': 'to'}],
            'approval_mode': 'operator_delegated',
            'unknown_recovery': 'authenticated_receipt_read',
        },
    }


def tools():
    return copy.deepcopy([*READ_DEFINITIONS, *MESSAGE_DEFINITIONS])


@contextmanager
def server(*, status_body=None, tool_list=None, close_after_initialize=False,
           init_result=None, initialized_status=202, wire_responses=None,
           content_types=None):
    observed = []
    current_status = status() if status_body is None else status_body
    current_tools = tools() if tool_list is None else tool_list

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_POST(self):
            amount = int(self.headers['Content-Length'])
            packet = json.loads(self.rfile.read(amount))
            observed.append((self.path, packet['method']))
            if packet['method'] == 'notifications/initialized':
                self.send_response(initialized_status)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if packet['method'] == 'initialize':
                result = init_result if init_result is not None else {
                    'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                    'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
            elif packet['method'] == 'tools/list':
                result = {'tools': current_tools}
            else:
                result = {'isError': False, 'structuredContent': current_status,
                          'content': [{'type': 'text', 'text': json.dumps(current_status, ensure_ascii=False)}]}
            data = json.dumps({'jsonrpc': '2.0', 'id': packet['id'], 'result': result}).encode()
            if wire_responses and packet['method'] in wire_responses:
                data = wire_responses[packet['method']].encode()
            self.send_response(200)
            self.send_header('Content-Type', (content_types or {}).get(packet['method'], 'application/json'))
            if close_after_initialize and packet['method'] == 'initialize':
                self.send_header('Connection', 'close')
                self.close_connection = True
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            return

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield http.server_port, observed
        finally:
            http.shutdown()
            thread.join(3)


def runner(*, owns=True, plist=PLIST, pid=4321, changed_after=None):
    state = {'launchctl': 0}

    def fake(argv, **kwargs):
        if argv[0] == '/bin/launchctl':
            state['launchctl'] += 1
            actual_pid = changed_after if changed_after and state['launchctl'] > 1 else pid
            stdout = f'gui/501/{LABEL} = {{\n\tpath = {plist}\n\tpid = {actual_pid}\n}}\n'
            return subprocess.CompletedProcess(argv, 0, stdout, '')
        assert argv[0] == '/usr/sbin/lsof'
        local = argv[-2].split(':')[-1]
        port = state.get('port')
        output = f'n127.0.0.1:{port}->127.0.0.1:{local}\n' if owns else ''
        return subprocess.CompletedProcess(argv, 0 if owns else 1, output, '')

    fake.state = state
    return fake


class ProcessProbeTests(unittest.TestCase):
    def call(self, *, status_body=None, tool_list=None, owns=True, plist=PLIST, changed_after=None):
        with server(status_body=status_body, tool_list=tool_list) as (port, observed):
            fake = runner(owns=owns, plist=plist, changed_after=changed_after)
            fake.state['port'] = port
            if owns and plist == PLIST and not changed_after and status_body is None and tool_list is None:
                result = module.probe(label=LABEL, plist_path=PLIST, port=port,
                                      pairing_secret=SECRET, expected=AUTH, runner=fake)
                self.assertEqual(result['recipient_count'], 1)
                self.assertEqual(result['pid'], 4321)
                self.assertEqual(result['tools_verified'], 6)
                self.assertTrue(result['verified'])
                self.assertNotIn(SECRET, json.dumps(result))
                self.assertEqual(len(observed), 4)
                return
            with self.assertRaises(module.ProbeError):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            if not owns or plist != PLIST or changed_after:
                self.assertEqual(observed, [])

    def test_launchctl_lookup_uses_absolute_system_binary(self):
        recorded = []

        def capture(argv, **kwargs):
            recorded.append(list(argv))
            return subprocess.CompletedProcess(
                argv, 0,
                f'gui/501/{LABEL} = {{\n\tpath = {PLIST}\n\tpid = 4321\n}}\n', '',
            )

        self.assertEqual(module.job_pid(LABEL, PLIST, runner=capture), 4321)
        self.assertEqual(recorded, [
            ['/bin/launchctl', 'print', f'gui/{module.os.getuid()}/{LABEL}'],
        ])

    def test_genuine_pid_and_effective_status(self):
        self.call()

    def test_wrong_port_owner_does_not_receive_secret(self):
        self.call(owns=False)

    def test_wrong_plist_does_not_receive_secret(self):
        self.call(plist='/another/process.plist')

    def test_launchctl_timeout_refuses_before_disclosing_secret(self):
        with server() as (port, observed):
            def timeout(argv, **kwargs):
                if argv[0] == '/bin/launchctl':
                    raise subprocess.TimeoutExpired(argv, 10)
                raise AssertionError('lsof must not run after a launchctl timeout')
            with self.assertRaises(module.ProbeError) as result:
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=timeout)
            self.assertEqual(str(result.exception), 'SERVICE_PROCESS_UNVERIFIED')
            self.assertEqual(observed, [])

    def test_lsof_timeout_refuses_before_disclosing_secret(self):
        with server() as (port, observed):
            healthy = runner()
            healthy.state['port'] = port
            def timeout(argv, **kwargs):
                if argv[0] == '/usr/sbin/lsof':
                    raise subprocess.TimeoutExpired(argv, 10)
                return healthy(argv, **kwargs)
            with self.assertRaises(module.ProbeError) as result:
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=timeout)
            self.assertEqual(str(result.exception), 'SERVICE_LISTENER_UNVERIFIED')
            self.assertEqual(observed, [])

    def test_launchd_pid_changed_does_not_receive_secret(self):
        self.call(changed_after=4322)

    def test_pid_changed_after_rpc_cannot_be_reported_live(self):
        with server() as (port, observed):
            stable = runner()
            stable.state['port'] = port

            def changed_after_status(argv, **kwargs):
                if argv[0] == '/bin/launchctl' and stable.state['launchctl'] == 2:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        f'gui/501/{LABEL} = {{\n\tpath = {PLIST}\n\tpid = 4322\n}}\n', '',
                    )
                return stable(argv, **kwargs)

            with self.assertRaisesRegex(module.ProbeError, 'SERVICE_PROCESS_CHANGED'):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH,
                             runner=changed_after_status)
            self.assertEqual(stable.state['launchctl'], 2)
            self.assertEqual([method for _, method in observed],
                             ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])

    def test_launchctl_unavailable_after_rpc_cannot_be_reported_live(self):
        with server() as (port, observed):
            stable = runner()
            stable.state['port'] = port

            def unavailable_after_status(argv, **kwargs):
                if argv[0] == '/bin/launchctl' and stable.state['launchctl'] == 2:
                    raise subprocess.TimeoutExpired(argv, 10)
                return stable(argv, **kwargs)

            with self.assertRaisesRegex(module.ProbeError, 'SERVICE_PROCESS_UNVERIFIED'):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH,
                             runner=unavailable_after_status)
            self.assertEqual(stable.state['launchctl'], 2)
            self.assertEqual([method for _, method in observed],
                             ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])

    def test_saved_grant_more_permissive_than_expected_fails(self):
        broken = status()
        broken['message_authority']['recipients'].append(
            {'target_agent_id': 99, 'expected_target_name': 'OtherAgent', 'kind': 'to'})
        self.call(status_body=broken)

    def test_status_text_and_structured_content_must_agree(self):
        safe = status()
        misleading = status()
        misleading['adapter']['spawn_enabled_on_server'] = True
        for content in (
            [{'type': 'text', 'text': json.dumps(misleading)}],
            [{'type': 'text', 'text': json.dumps(safe).replace('"fresh"', '"legacy"')}],
            [{'type': 'text', 'text': 'not JSON'}],
            [{'type': 'text', 'text': json.dumps(safe)}, {'type': 'text', 'text': json.dumps(safe)}],
            [],
        ):
            with self.subTest(content=content):
                wire = json.dumps({'jsonrpc': '2.0', 'id': 3, 'result': {
                    'isError': False, 'structuredContent': safe, 'content': content,
                }})
                with server(wire_responses={'tools/call': wire}) as (port, observed):
                    fake = runner()
                    fake.state['port'] = port
                    with self.assertRaises(module.ProbeError):
                        module.probe(label=LABEL, plist_path=PLIST, port=port,
                                     pairing_secret=SECRET, expected=AUTH, runner=fake)
                    self.assertEqual([method for _, method in observed],
                                     ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])

    def test_status_content_must_not_hide_invalid_types(self):
        safe = status()
        textual = status()
        textual['adapter']['message_enabled_on_server'] = 1
        wire = json.dumps({'jsonrpc': '2.0', 'id': 3, 'result': {
            'isError': False, 'structuredContent': safe,
            'content': [{'type': 'text', 'text': json.dumps(textual)}],
        }})
        with server(wire_responses={'tools/call': wire}) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaises(module.ProbeError):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)

    def test_discovery_cursor_cannot_hide_extra_tools(self):
        for cursor in ('another-page', None):
            with self.subTest(cursor=cursor):
                result = {'tools': tools(), 'nextCursor': cursor}
                wire = json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': result})
                with server(wire_responses={'tools/list': wire}) as (port, observed):
                    fake = runner()
                    fake.state['port'] = port
                    with self.assertRaises(module.ProbeError):
                        module.probe(label=LABEL, plist_path=PLIST, port=port,
                                     pairing_secret=SECRET, expected=AUTH, runner=fake)
                    self.assertEqual([name for _, name in observed],
                                     ['initialize', 'notifications/initialized', 'tools/list'])

    def test_discovery_must_not_advertise_extra_send_arguments(self):
        altered = tools()
        commit = next(tool for tool in altered if tool['name'] == 'orrery_message_commit')
        commit['inputSchema']['properties']['shell_command'] = {'type': 'string'}
        self.call(tool_list=altered)

    def test_discovery_rejects_weakened_preview_argument_constraints(self):
        altered = tools()
        preview = next(tool for tool in altered if tool['name'] == 'orrery_message_preview')
        preview['inputSchema']['properties']['body_md'].pop('maxLength')
        self.call(tool_list=altered)

    def test_discovery_rejects_misleading_capability_annotations(self):
        for field in ('annotations', 'securitySchemes'):
            with self.subTest(field=field):
                altered = tools()
                commit = next(tool for tool in altered if tool['name'] == 'orrery_message_commit')
                if field == 'annotations':
                    commit['annotations']['readOnlyHint'] = True
                else:
                    commit['securitySchemes'] = [{'type': 'oauth2'}]
                self.call(tool_list=altered)

    def test_discovery_rejects_read_schema_with_unbounded_parameters(self):
        altered = tools()
        listing = next(tool for tool in altered if tool['name'] == 'orrery_list_agents')
        listing['inputSchema']['additionalProperties'] = True
        self.call(tool_list=altered)

    def test_wrong_sender_fails(self):
        broken = status()
        broken['message_authority']['sender_agent_id'] = 12
        self.call(status_body=broken)

    def test_wrong_mail_instance_fails(self):
        broken = status()
        broken['dashboard']['mail_instance_id'] = 'f' * 32
        self.call(status_body=broken)

    def test_wrong_runtime_mode_fails(self):
        broken = status()
        broken['dashboard']['runtime_mode'] = 'legacy'
        self.call(status_body=broken)

    def test_spawn_enabled_fails(self):
        broken = status()
        broken['adapter']['spawn_enabled_on_server'] = True
        self.call(status_body=broken)

    def test_no_message_commit_fails(self):
        bad = [v for v in tools() if v['name'] != 'orrery_message_commit']
        self.call(tool_list=bad)

    def test_extra_arbitrary_tool_fails(self):
        bad = tools() + [{'name': 'exec_shell'}]
        self.call(tool_list=bad)

    def test_duplicate_tool_fails(self):
        bad = tools()[:-1] + [tools()[0]]
        self.call(tool_list=bad)

    def test_false_readonly_fails(self):
        broken = status()
        broken['adapter']['read_only'] = True
        self.call(status_body=broken)

    def test_numeric_adapter_flags_never_count_as_boolean_authority(self):
        for numeric_type in (int, float):
            for field, number in (
                ('read_only', 0),
                ('spawn_enabled_on_server', 0),
                ('message_enabled_on_server', 1),
            ):
                with self.subTest(field=field, numeric_type=numeric_type.__name__):
                    broken = status()
                    broken['adapter'][field] = numeric_type(number)
                    self.call(status_body=broken)

    def test_wrong_recovery_contract_fails(self):
        broken = status()
        broken['message_authority']['unknown_recovery'] = 'replay_automatically'
        self.call(status_body=broken)

    def test_connection_close_cannot_trigger_unverified_reconnect(self):
        with server(close_after_initialize=True) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaises(module.ProbeError):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual(len(observed), 1)

    def test_wrong_initialize_protocol_refuses_before_discovery(self):
        init = {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                'protocolVersion': 'unsupported', 'capabilities': {'tools': {}}}
        with server(init_result=init) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaises(module.ProbeError):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual([method for _, method in observed], ['initialize'])

    def test_rejected_initialization_notification_refuses_before_discovery(self):
        with server(initialized_status=200) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaises(module.ProbeError):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual([method for _, method in observed],
                             ['initialize', 'notifications/initialized'])

    def test_boolean_response_id_cannot_impersonate_numeric_id(self):
        # Python's True == 1 must not satisfy JSON-RPC's numeric request id.
        result = {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                  'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
        wire = json.dumps({'jsonrpc': '2.0', 'id': True, 'result': result})
        with server(wire_responses={'initialize': wire}) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaisesRegex(module.ProbeError, 'MCP_STATUS_UNVERIFIED|MCP_PROTOCOL_UNVERIFIED'):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual([method for _, method in observed], ['initialize'])

    def test_rpc_result_cannot_coexist_with_error_at_any_step(self):
        init = {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
        results = {'initialize': init, 'tools/list': {'tools': tools()},
                   'tools/call': {'isError': False, 'structuredContent': status()}}
        sequence = ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']
        for method, result in results.items():
            with self.subTest(method=method):
                rpc_id = {'initialize': 1, 'tools/list': 2, 'tools/call': 3}[method]
                wire = json.dumps({'jsonrpc': '2.0', 'id': rpc_id,
                                   'result': result, 'error': {'code': -32603, 'message': 'failure'}})
                with server(wire_responses={method: wire}) as (port, observed):
                    fake = runner()
                    fake.state['port'] = port
                    with self.assertRaises(module.ProbeError):
                        module.probe(label=LABEL, plist_path=PLIST, port=port,
                                     pairing_secret=SECRET, expected=AUTH, runner=fake)
                    self.assertEqual([value for _, value in observed], sequence[:sequence.index(method) + 1])

    def test_json_content_type_with_utf8_parameter_is_accepted(self):
        headers = {method: 'application/json; charset=utf-8'
                   for method in ('initialize', 'tools/list', 'tools/call')}
        with server(content_types=headers) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            result = module.probe(label=LABEL, plist_path=PLIST, port=port,
                                  pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertTrue(result['verified'])
            self.assertEqual(len(observed), 4)

    def test_non_json_content_type_refuses_at_each_rpc_step(self):
        sequence = ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']
        for method in ('initialize', 'tools/list', 'tools/call'):
            with self.subTest(method=method):
                with server(content_types={method: 'text/plain'}) as (port, observed):
                    fake = runner()
                    fake.state['port'] = port
                    with self.assertRaises(module.ProbeError):
                        module.probe(label=LABEL, plist_path=PLIST, port=port,
                                     pairing_secret=SECRET, expected=AUTH, runner=fake)
                    self.assertEqual([name for _, name in observed], sequence[:sequence.index(method) + 1])

    def test_nonfinite_json_literals_refuse_at_each_rpc_step(self):
        results = {
            'initialize': {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                           'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}},
            'tools/list': {'tools': tools()},
            'tools/call': {'isError': False, 'structuredContent': status()},
        }
        sequence = ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']
        for method, original_result in results.items():
            for constant in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(method=method, constant=str(constant)):
                    rpc_id = {'initialize': 1, 'tools/list': 2, 'tools/call': 3}[method]
                    result = {**original_result, 'unexpected': constant}
                    wire = json.dumps({'jsonrpc': '2.0', 'id': rpc_id, 'result': result})
                    with server(wire_responses={method: wire}) as (port, observed):
                        fake = runner()
                        fake.state['port'] = port
                        with self.assertRaises(module.ProbeError):
                            module.probe(label=LABEL, plist_path=PLIST, port=port,
                                         pairing_secret=SECRET, expected=AUTH, runner=fake)
                        self.assertEqual([name for _, name in observed], sequence[:sequence.index(method) + 1])

    def test_json_exponent_overflow_refuses_at_every_rpc_step(self):
        results = {
            'initialize': {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                           'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}},
            'tools/list': {'tools': tools()},
            'tools/call': {'isError': False, 'structuredContent': status()},
        }
        sequence = ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']
        for method, result in results.items():
            for exponent in ('1e999', '-1e999'):
                with self.subTest(method=method, exponent=exponent):
                    rpc_id = {'initialize': 1, 'tools/list': 2, 'tools/call': 3}[method]
                    raw = json.dumps({'jsonrpc': '2.0', 'id': rpc_id,
                                      'result': {**result, 'unused': 0.0}})
                    raw = raw.replace('"unused": 0.0', f'"unused": {exponent}')
                    with server(wire_responses={method: raw}) as (port, observed):
                        fake = runner()
                        fake.state['port'] = port
                        with self.assertRaises(module.ProbeError):
                            module.probe(label=LABEL, plist_path=PLIST, port=port,
                                         pairing_secret=SECRET, expected=AUTH, runner=fake)
                        self.assertEqual([name for _, name in observed],
                                         sequence[:sequence.index(method) + 1])

    def test_normal_finite_json_exponent_remains_accepted(self):
        init = {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
        wire = json.dumps({'jsonrpc': '2.0', 'id': 1,
                           'result': {**init, 'unused': 0.0}})
        wire = wire.replace('"unused": 0.0', '"unused": 1e2')
        with server(wire_responses={'initialize': wire}) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            result = module.probe(label=LABEL, plist_path=PLIST, port=port,
                                  pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertTrue(result['verified'])
            self.assertEqual(len(observed), 4)

    def test_overdeep_json_is_reported_as_probe_refusal(self):
        init = {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
        wire = json.dumps({'jsonrpc': '2.0', 'id': 1,
                           'result': {**init, 'unused': 0}})
        wire = wire.replace('"unused": 0', '"unused": ' + '[' * 20000 + '0' + ']' * 20000)
        with server(wire_responses={'initialize': wire}) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaises(module.ProbeError):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual([method for _, method in observed], ['initialize'])

    def test_duplicate_jsonrpc_id_is_rejected_even_when_values_match(self):
        # Duplicate JSON object members are ambiguous across clients.
        result = {'serverInfo': {'name': 'orrery-chatgpt-mcp'},
                  'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
        wire = ('{"jsonrpc":"2.0","id":1,"id":1,"result":'
                + json.dumps(result) + '}')
        with server(wire_responses={'initialize': wire}) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaisesRegex(module.ProbeError, 'MCP_STATUS_UNVERIFIED|MCP_PROTOCOL_UNVERIFIED'):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual([method for _, method in observed], ['initialize'])

    def test_duplicate_nested_authority_member_is_rejected(self):
        # The apparently valid last value must not hide an earlier grant.
        status_json = json.dumps(status())
        assert '"runtime_mode": "fresh"' in status_json
        status_json = status_json.replace(
            '"runtime_mode": "fresh"',
            '"runtime_mode": "legacy", "runtime_mode": "fresh"',
        )
        wire = ('{"jsonrpc":"2.0","id":3,"result":{"isError":false,'
                '"structuredContent":' + status_json + '}}')
        with server(wire_responses={'tools/call': wire}) as (port, observed):
            fake = runner()
            fake.state['port'] = port
            with self.assertRaisesRegex(module.ProbeError, 'MCP_STATUS_UNVERIFIED|MCP_PROTOCOL_UNVERIFIED'):
                module.probe(label=LABEL, plist_path=PLIST, port=port,
                             pairing_secret=SECRET, expected=AUTH, runner=fake)
            self.assertEqual([method for _, method in observed],
                             ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'])

    def test_bool_sender_rejected(self):
        broken = status()
        broken['message_authority']['sender_agent_id'] = True
        self.call(status_body=broken)

    def test_duplicate_recipient_in_live_status_fails(self):
        broken = status()
        broken['message_authority']['recipients'].append(dict(broken['message_authority']['recipients'][0]))
        self.call(status_body=broken)


if __name__ == '__main__':
    unittest.main()
