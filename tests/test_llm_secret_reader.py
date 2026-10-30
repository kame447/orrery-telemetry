"""偽の参照とrunnerだけで確認し、OS storeや実鍵には接続しない。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('secret_reader_fixture', ROOT / 'hooks/llm_secret_reader.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
REF = {'store': 'secret-service', 'attributes': {'service': 'fixture-service', 'account': 'fixture-account'}}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    tool = tmp_path / 'secret-tool'
    tool.write_text('synthetic executable, never run')
    tool.chmod(0o700)
    monkeypatch.setattr(reader, 'SECRET_TOOL', str(tool))
    monkeypatch.setattr(reader.sys, 'platform', 'linux')
    return tool


@pytest.mark.parametrize('ref', [None, {}, {'store': 'file', 'attributes': {}},
    {'store': 'secret-service', 'attributes': {}},
    {'store': 'secret-service', 'attributes': {'a': 'bad\nvalue'}},
    {'store': 'secret-service', 'attributes': {'a': 1}},
    {**REF, 'key': 'must-not-be-a-value'},
    {'store': 'secret-service', 'attributes': {'--all': 'x'}},
    {'store': 'secret-service', 'attributes': {'service': '--all'}}])
def test_rejects_unknown_or_value_shaped_references(ref):
    with pytest.raises(ValueError, match='^invalid_credential_ref$'):
        reader.validate_ref(ref)


def test_lookup_uses_selected_argv_and_minimal_context(prepared, monkeypatch):
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/fixture/bus')
    monkeypatch.setenv('XDG_RUNTIME_DIR', '/fixture/runtime')
    monkeypatch.setenv('AMBIENT_PROVIDER_KEY', 'ambient-must-not-be-used')
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'synthetic-reader-value\n')
    assert reader.read_ref(REF, runner=run) == 'synthetic-reader-value'
    argv, options = calls[0]
    assert argv == [str(prepared), 'lookup', 'account', 'fixture-account', 'service', 'fixture-service']
    assert options['stdin'] == subprocess.DEVNULL and options['timeout'] == 5
    assert options['env']['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/fixture/bus'
    assert options['env']['XDG_RUNTIME_DIR'] == '/fixture/runtime'
    assert set(options['env']) == {'HOME', 'PATH', 'LANG', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR'}
    assert 'synthetic-reader-value' not in json.dumps((argv, options))


@pytest.mark.parametrize('rc,output', [(1, b'private-error'), (0, b''), (0, b'\xff'),
    (0, b'first\nsecond\n'), (0, b'key\x00'), (0, b'x' * 65537)])
def test_failure_never_returns_ambient_or_raw_output(prepared, rc, output):
    calls = []
    def run(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=rc, stdout=output)
    with pytest.raises(ValueError) as error:
        reader.read_ref(REF, runner=run)
    assert str(error.value) == 'credential_unavailable'
    assert calls == [1]


def test_timeout_is_fixed_and_has_no_helper_output(prepared):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(['private-canary'], 5, output=b'private-output')
    with pytest.raises(ValueError) as error:
        reader.read_ref(REF, runner=run)
    assert str(error.value) == 'credential_timeout'
    assert error.value.__suppress_context__


def test_static_check_does_not_lookup_and_rejects_missing_tool(prepared, monkeypatch):
    monkeypatch.setattr(reader.subprocess, 'run', lambda *a, **k: pytest.fail('static check read a key'))
    reader.validate_ref(REF)
    reader.validate_environment()
    prepared.unlink()
    with pytest.raises(ValueError, match='^credential_reader_unavailable$'):
        reader.validate_environment()
    monkeypatch.setattr(reader.sys, 'platform', 'darwin')
    with pytest.raises(ValueError, match='^unsupported_credential_store$'):
        reader.validate_environment()


def test_internal_reader_command_and_manifest_select_only_one_backend(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'backends': {'deepseek': {'credential_ref': REF},
        'strata': {'credential_ref': None}}}))
    assert reader.manifest_ref(manifest, 'deepseek') == REF
    with pytest.raises(ValueError, match='^invalid_credential_ref$'):
        reader.manifest_ref(manifest, 'strata')
    argv = reader.command(ROOT, manifest, 'deepseek', sys.executable)
    assert argv == [sys.executable, '-I', '-B', str(ROOT / 'hooks/llm_secret_reader.py'),
                    '--manifest', str(manifest), '--backend', 'deepseek']


def test_installed_reader_minimal_environment_with_synthetic_store(tmp_path):
    import os
    import shutil
    target = tmp_path / 'installed/hooks/llm_secret_reader.py'
    target.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'hooks/llm_secret_reader.py', target)
    shutil.copyfile(ROOT / 'hooks/native_connections.py', target.with_name('native_connections.py'))
    store = tmp_path / 'fake-secret-tool'
    store.write_text('#!/bin/sh\nprintf "synthetic-e1-value\\n"\n')
    store.chmod(0o700)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'backends': {'deepseek': {'credential_ref': REF}}}))
    code = ('import importlib.util,sys; '
            's=importlib.util.spec_from_file_location("reader",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
            'm.SECRET_TOOL=sys.argv[2];m.sys.platform="linux";sys.argv=[sys.argv[1],"--manifest",sys.argv[3],"--backend","deepseek"]; '
            'raise SystemExit(m.main())')
    done = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(target), str(store), str(manifest)],
        cwd='/', env={'HOME': str(tmp_path), 'PATH': os.defpath}, stdin=subprocess.DEVNULL,
        text=True, capture_output=True, timeout=10)
    assert done.returncode == 0 and done.stdout == 'synthetic-e1-value\n' and done.stderr == ''


OP_REF = {'store': '1password', 'reference': 'op://Development/DeepSeek API/credential'}


@pytest.fixture
def prepared_onepassword(tmp_path, monkeypatch):
    tool = tmp_path / 'op'
    tool.write_text('synthetic executable, never invoked')
    tool.chmod(0o700)
    monkeypatch.setattr(reader, 'OP_CLI_CANDIDATES', (str(tool),))
    monkeypatch.setattr(reader.sys, 'platform', 'darwin')
    return tool


@pytest.mark.parametrize('ref', [
    {'store': '1password', 'reference': 'op://vault/item'},
    {'store': '1password', 'reference': 'op://vault//field'},
    {'store': '1password', 'reference': 'op://vault/../field'},
    {'store': '1password', 'reference': 'op://vault/item/field\nother'},
    {'store': '1password', 'reference': 'op://vault/item/field?attribute=value'},
    {'store': '1password', 'reference': 'https://vault/item/field'},
    {'store': '1password', 'reference': 'op://vault/item/field', 'api_key': 'private'},
    {'store': '1password', 'reference': None},
])
def test_onepassword_rejects_invalid_or_value_shaped_refs(ref):
    with pytest.raises(ValueError, match='^invalid_credential_ref$'):
        reader.validate_ref(ref)


def test_onepassword_reads_only_selected_ref_with_whitelisted_environment(prepared_onepassword, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'ambient-must-not-be-used')
    monkeypatch.setenv('OP_SERVICE_ACCOUNT_TOKEN', 'ambient-service-token-must-not-pass')
    monkeypatch.setenv('OP_ACCOUNT', 'personal-account')
    monkeypatch.setenv('OP_BIOMETRIC_UNLOCK_ENABLED', 'true')
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'synthetic-onepassword-key\n')
    assert reader.read_ref(OP_REF, runner=run) == 'synthetic-onepassword-key'
    argv, options = calls[0]
    assert argv == [str(prepared_onepassword), 'read', OP_REF['reference']]
    assert options['stdin'] == subprocess.DEVNULL and options['timeout'] == 20
    assert set(options['env']) == {'HOME', 'PATH', 'LANG', 'OP_ACCOUNT', 'OP_BIOMETRIC_UNLOCK_ENABLED'}
    assert not any('synthetic-onepassword-key' in str(value) for value in (argv, options))


def test_onepassword_static_check_requires_known_executable_but_never_reads_key(prepared_onepassword, monkeypatch):
    monkeypatch.setattr(reader.subprocess, 'run', lambda *args, **kwargs: pytest.fail('static check read a key'))
    reader.validate_ref(OP_REF)
    reader.validate_environment(OP_REF)
    prepared_onepassword.unlink()
    with pytest.raises(ValueError, match='^credential_reader_unavailable$'):
        reader.validate_environment(OP_REF)
    monkeypatch.setattr(reader.sys, 'platform', 'linux')
    with pytest.raises(ValueError, match='^unsupported_credential_store$'):
        reader.validate_environment(OP_REF)


def test_onepassword_failed_read_reports_only_sanitized_error(prepared_onepassword):
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b'private-error')
    with pytest.raises(ValueError, match='^credential_unavailable$'):
        reader.read_ref(OP_REF, runner=run)


SA_REF = {
    'store': '1password-service-account',
    'reference': 'op://Orrery/DeepSeek/credential',
    'keychain': {'service': 'orrery-1password-service-account', 'account': 'deepseek'},
}


def test_service_account_keychain_bootstrap_injects_only_into_op_process(prepared_onepassword, tmp_path, monkeypatch):
    keychain = tmp_path / 'security'
    keychain.write_text('synthetic binary')
    keychain.chmod(0o700)
    monkeypatch.setattr(reader, 'MAC_SECURITY', str(keychain))
    monkeypatch.setenv('OP_SERVICE_ACCOUNT_TOKEN', 'ambient-must-be-ignored')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'ambient-provider-key-must-be-ignored')
    calls = []
    def run(argv, **opts):
        calls.append((argv, opts))
        if argv[0] == str(keychain):
            return SimpleNamespace(returncode=0, stdout=b'ops_synthetic-test-service-credential\n')
        assert argv == [str(prepared_onepassword), 'read', SA_REF['reference']]
        assert opts['env'].get('OP_SERVICE_ACCOUNT_TOKEN') == 'ops_synthetic-test-service-credential'
        assert 'DEEPSEEK_API_KEY' not in opts['env']
        return SimpleNamespace(returncode=0, stdout=b'synthetic-deepseek-provider-secret\n')
    assert reader.read_ref(SA_REF, runner=run) == 'synthetic-deepseek-provider-secret'
    assert len(calls) == 2
    assert calls[0][0] == [str(keychain), 'find-generic-password', '-s',
                            SA_REF['keychain']['service'], '-a',
                            SA_REF['keychain']['account'], '-w']
    assert calls[0][1]['stdin'] == subprocess.DEVNULL
    assert calls[0][1]['timeout'] == 8 and calls[1][1]['timeout'] == 20
    assert set(calls[0][1]['env']) == {'HOME', 'PATH', 'LANG'}


@pytest.mark.parametrize('ref', [
    {'store': '1password-service-account', 'reference': 'op://a/b/c'},
    {**SA_REF, 'keychain': {'service': '../bad', 'account': 'deepseek'}},
    {**SA_REF, 'keychain': {'service': 'valid', 'account': 'bad name'}},
    {**SA_REF, 'keychain': {'service': 'valid', 'account': 'ok', 'token': 'secret'}},
    {**SA_REF, 'reference': 'op://bad//field'},
    {**SA_REF, 'token': 'do-not-store-token-in-manifest'},
])
def test_service_account_rejects_malformed_or_embedded_secret_ref(ref):
    with pytest.raises(ValueError, match='^invalid_credential_ref$'):
        reader.validate_ref(ref)


def test_service_account_missing_token_never_uses_desktop_fallback(prepared_onepassword, tmp_path, monkeypatch):
    keychain = tmp_path / 'security'
    keychain.write_text('synthetic binary')
    keychain.chmod(0o700)
    monkeypatch.setattr(reader, 'MAC_SECURITY', str(keychain))
    calls = []
    def run(argv, **opts):
        calls.append(argv)
        return SimpleNamespace(returncode=44, stdout=b'', stderr=b'private details')
    with pytest.raises(ValueError, match='^credential_unavailable$'):
        reader.read_ref(SA_REF, runner=run)
    assert len(calls) == 1 and calls[0][0] == str(keychain)


def test_service_account_static_check_never_reads_key(prepared_onepassword, tmp_path, monkeypatch):
    keychain = tmp_path / 'security'
    keychain.write_text('synthetic binary')
    keychain.chmod(0o700)
    monkeypatch.setattr(reader, 'MAC_SECURITY', str(keychain))
    monkeypatch.setattr(reader.subprocess, 'run', lambda *a, **kw: pytest.fail('must not read token'))
    reader.validate_ref(SA_REF)
    reader.validate_environment(SA_REF)
    keychain.unlink()
    with pytest.raises(ValueError, match='^credential_reader_unavailable$'):
        reader.validate_environment(SA_REF)
