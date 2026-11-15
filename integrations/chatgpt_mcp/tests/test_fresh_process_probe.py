"""Synthetic loopback and launchd/lsof proof tests; no installed processes."""
from __future__ import annotations

from contextlib import contextmanager
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
    return [{'name': n} for n in sorted(module.READ_TOOLS | module.MESSAGE_TOOLS)]


@contextmanager
def server(*, status_body=None, tool_list=None, close_after_initialize=False,
           init_result=None, initialized_status=202):
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
                result = {'isError': False, 'structuredContent': current_status}
            data = json.dumps({'jsonrpc': '2.0', 'id': packet['id'], 'result': result}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
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
        if argv[0] == 'launchctl':
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

    def test_genuine_pid_and_effective_status(self):
        self.call()

    def test_wrong_port_owner_does_not_receive_secret(self):
        self.call(owns=False)

    def test_wrong_plist_does_not_receive_secret(self):
        self.call(plist='/another/process.plist')

    def test_launchd_pid_changed_does_not_receive_secret(self):
        self.call(changed_after=4322)

    def test_saved_grant_more_permissive_than_expected_fails(self):
        broken = status()
        broken['message_authority']['recipients'].append(
            {'target_agent_id': 99, 'expected_target_name': 'OtherAgent', 'kind': 'to'})
        self.call(status_body=broken)

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
