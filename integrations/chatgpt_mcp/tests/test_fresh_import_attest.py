"""Real gateway release and fresh Mail imports with isolated source snapshots."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import sysconfig

import pytest

from orrery_chatgpt_mcp import service
from orrery_chatgpt_mcp.fresh_import_attest import (
    ImportAttestationError, ImportPlan, check, package_digest,
)
from orrery_chatgpt_mcp.fresh_release_plan import Plan


def case(tmp_path):
    root = tmp_path.resolve()
    releases = root / 'releases'
    releases.mkdir(mode=0o700)
    source = Path(__file__).resolve().parents[1] / 'src'
    release, release_digest = service.install_release(source, releases, source_commit='a'*40)
    mail_source = Path(__file__).resolve().parents[3] / 'packages/agentstack_mail/src/agentstack_mail'
    mail_dir = root / 'bundled-mail' / 'agentstack_mail'
    mail_dir.parent.mkdir(mode=0o700)
    mail_dir.mkdir(mode=0o700)
    for path in mail_source.glob('*.py'):
        shutil.copy2(path, mail_dir / path.name)
        (mail_dir / path.name).chmod(0o444)
    mail_dir.chmod(0o555)
    state = root / 'state'
    state.mkdir(mode=0o700)
    (state/'pairing.json').write_text(json.dumps({'secret':'A'*43}))
    (state/'pairing.json').chmod(0o600)
    policy = state/'policy.json'
    policy.write_text(json.dumps({'kind':'orrery-mcp-fresh-message-policy-v1',
        'context_path':str(state/'prepared'/'context.json'),
        'mail_instance_id':'1'*32,'sender_agent_id':7,
        'recipients':[{'target_agent_id':8,'expected_target_name':'AgentTwo','kind':'to'}]}))
    policy.chmod(0o600)
    plan = Plan(python=Path(sys.executable).resolve(), release_dir=release,
                release_digest=release_digest, source_commit='a'*40,
                state_dir=state, policy_path=policy,
                label='org.agentstack.chatgpt-mcp-fresh.fixture', port=8792,
                mail_instance_id='1'*32, sender_agent_id=7,
                recipients=((8,'AgentTwo','to'),))
    deps = ImportPlan(mail_package=mail_dir, mail_digest=package_digest(mail_dir),
                      site_packages=Path(sysconfig.get_path('purelib')).resolve())
    def checked(plan=plan, dependency=deps, **kwargs):
        return check(plan, dependency, verify_release=service.verify_release,
                     read_release=service.read_release, **kwargs)
    return plan, deps, checked


def test_real_immutable_gateway_and_mail_dependency_imports(tmp_path):
    plan, dep, checked = case(tmp_path)
    result = checked()
    assert result == {'validated': True, 'gateway_import': True, 'mail_import': True,
                      'receipt_contract': True, 'import_origins_verified': True,
                      'third_party_pinned': False, 'live_process_verified': False,
                      'service_started': False, 'operation': 'offline_import_preflight'}
    assert service.verify_release(plan.release_dir, plan.release_digest, str(plan.python))
    assert package_digest(dep.mail_package) == dep.mail_digest


def test_changed_mail_source_is_refused(tmp_path):
    _, dep, checked = case(tmp_path)
    path=dep.mail_package/'fresh_client.py'
    path.chmod(0o600)
    path.write_bytes(path.read_bytes()+b'\n# changed source\n')
    path.chmod(0o444)
    with pytest.raises(ImportAttestationError, match='ATTESTATION_REFUSED'):checked()


def test_writable_or_symlinked_mail_sources_are_refused(tmp_path):
    _, dep, checked = case(tmp_path)
    path=dep.mail_package/'fresh_client.py'
    path.chmod(0o666)
    with pytest.raises(ImportAttestationError):checked()
    path.chmod(0o444)
    dep.mail_package.chmod(0o700)
    renamed=path.with_suffix('.private');path.rename(renamed)
    path.symlink_to(renamed)
    dep.mail_package.chmod(0o555)
    with pytest.raises(ImportAttestationError):checked()


def test_bytecode_cache_is_rejected_before_any_import(tmp_path):
    _, dep, checked = case(tmp_path)
    dep.mail_package.chmod(0o700)
    (dep.mail_package/'__pycache__').mkdir(mode=0o700)
    dep.mail_package.chmod(0o555)
    with pytest.raises(ImportAttestationError):checked()


def test_missing_receipt_contract_is_refused_before_serve(tmp_path):
    _, dep, checked = case(tmp_path)
    path=dep.mail_package/'fresh_message_receipts.py'
    dep.mail_package.chmod(0o700)
    path.unlink()
    dep.mail_package.chmod(0o555)
    derived=replace(dep,mail_digest=package_digest(dep.mail_package))
    with pytest.raises(ImportAttestationError):checked(dependency=derived)


def test_invalid_site_packages_never_falls_back_to_host_paths(tmp_path):
    _, dep, checked = case(tmp_path)
    empty=(tmp_path/'no-third-party').resolve();empty.mkdir(mode=0o700)
    with pytest.raises(ImportAttestationError):checked(dependency=replace(dep,site_packages=empty))


def test_no_credentials_in_command_or_environment(tmp_path,monkeypatch):
    plan, dep, checked = case(tmp_path)
    monkeypatch.setenv('AGENTSTACK_MAIL_DB','private-db-location')
    monkeypatch.setenv('AGENTSTACK_TOKEN','private-sender-token')
    captured=[]
    def runner(args,**kwargs):
        captured.append((args,kwargs))
        return subprocess.run(args,**kwargs)
    assert checked(runner=runner)['validated'] is True
    assert len(captured)==1
    cmd,kwargs=captured[0]
    assert cmd[1:4]==['-I','-S','-B']
    assert kwargs['timeout']==30
    assert 'AGENTSTACK_TOKEN' not in kwargs['env']
    assert all('private-' not in str(v) for v in (*cmd,*kwargs['env'].values()))


def test_release_tamper_and_source_directory_refused(tmp_path):
    plan, dep, checked = case(tmp_path)
    gateway=plan.release_dir/'orrery_chatgpt_mcp'/'fresh_server.py'
    gateway.chmod(0o600)
    gateway.write_bytes(gateway.read_bytes()+b'\n# tamper\n')
    gateway.chmod(0o444)
    with pytest.raises(ImportAttestationError):checked()


def test_subprocess_failure_is_sanitized(tmp_path):
    _, _, checked = case(tmp_path)
    def failed(*args,**kwargs):
        return subprocess.CompletedProcess(args[0],78,'','SECRET_FROM_SUBPROCESS')
    with pytest.raises(ImportAttestationError) as exc:checked(runner=failed)
    assert 'SECRET_FROM_SUBPROCESS' not in str(exc.value)


def test_context_does_not_require_existing_fresh_service(tmp_path):
    plan, dep, checked = case(tmp_path)
    assert not (plan.state_dir/'prepared'/'context.json').exists()
    assert checked()['service_started'] is False
    assert not any(plan.state_dir.glob('*.sqlite3'))
