"""Host/isolated-child contract for operator-approved dependency manifests."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sysconfig

import pytest

from orrery_chatgpt_mcp.fresh_dependency_attest import inspect as inspect_bundle
from orrery_chatgpt_mcp.fresh_import_attest import ImportAttestationError
from test_fresh_import_attest import case
from test_fresh_dependency_attest import fixture as synthetic_bundle, freeze


def pinned(tmp_path):
    plan, dep, checked = case(tmp_path)
    site, manifest = synthetic_bundle(tmp_path)
    return plan, replace(dep, site_packages=site, dependency_manifest=manifest), checked, site, manifest


def child_result(pin):
    return {'gateway_import':True, 'mail_import':True, 'receipt_contract':True,
            'import_origins_verified':True, 'third_party_pinned':pin}


def test_pinned_contract_is_passed_to_isolated_child_without_live_authority(tmp_path):
    plan, dep, checked, site, manifest = pinned(tmp_path)
    invoked = []
    def verified_child(args, **kwargs):
        invoked.append((args,kwargs))
        assert args[:4] == [str(plan.python), '-I', '-S', '-B']
        assert args[-2] == str(manifest)
        assert args[-1] == inspect_bundle(site)['sha256']
        assert kwargs['env'] == {'PATH':'/usr/bin:/bin', 'HOME':'/nonexistent',
                                 'PYTHONDONTWRITEBYTECODE':'1'}
        return subprocess.CompletedProcess(args,0,json.dumps(child_result(True)),'')
    result=checked(dependency=dep,runner=verified_child)
    assert result['validated'] and result['third_party_pinned']
    assert result['live_process_verified'] is False and result['service_started'] is False
    assert len(invoked)==1


@pytest.mark.parametrize('change',['file','manifest','different_site','writable_site'])
def test_unapproved_dependency_snapshot_never_starts_child(tmp_path,change):
    _, dep, checked, site, manifest = pinned(tmp_path)
    if change=='file':
        site.chmod(0o700)
        path=site/'mcp'/'__init__.py';path.chmod(0o600)
        path.write_text('import tampered')
        freeze(site)
    elif change=='manifest':
        manifest.chmod(0o644)
    elif change=='different_site':
        dep=replace(dep,site_packages=tmp_path.resolve()/'not-installed')
    else:
        site.chmod(0o700)
    def forbidden(*args,**kwargs):
        pytest.fail('unapproved dependencies must fail before child process starts')
    with pytest.raises(ImportAttestationError,match='ATTESTATION_REFUSED'):
        checked(dependency=dep,runner=forbidden)


def test_pinned_child_must_prove_pinned_not_just_imported(tmp_path):
    _, dep, checked, _, _ = pinned(tmp_path)
    def unpinned_child(args,**kwargs):
        return subprocess.CompletedProcess(args,0,json.dumps(child_result(False)),'')
    with pytest.raises(ImportAttestationError):checked(dependency=dep,runner=unpinned_child)


def test_mutation_during_child_is_refused_after_child_returns(tmp_path):
    _,dep,checked,site,_=pinned(tmp_path)
    def changed_midflight(args,**kwargs):
        site.chmod(0o700)
        p=site/'mcp'/'__init__.py';p.chmod(0o600);p.write_text('changed during child')
        freeze(site)
        return subprocess.CompletedProcess(args,0,json.dumps(child_result(True)),'')
    with pytest.raises(ImportAttestationError):checked(dependency=dep,runner=changed_midflight)


def test_synthetic_bundle_cannot_fake_actual_import(tmp_path):
    _,dep,checked,_,_=pinned(tmp_path)
    with pytest.raises(ImportAttestationError):checked(dependency=dep)


@pytest.mark.skipif(os.getenv('ORRERY_MCP_TEST_REAL_DEPENDENCY_PIN')!='1',
                    reason='requires an explicitly copied, owner-controlled installed dependency snapshot')
def test_real_frozen_site_import_and_double_verify(tmp_path):
    """Opt-in because copying all real dependencies is heavier than the unit suite."""
    _,dep,checked=case(tmp_path)
    source=Path(sysconfig.get_path('purelib')).resolve()
    site=tmp_path.resolve()/'immutable-installed-site'
    def ignore(directory,names):
        return {name for name in names if name=='__pycache__' or name.endswith(
            ('.pth','.egg-link','.pyc','.pyo'))}
    shutil.copytree(source,site,ignore=ignore)
    freeze(site)
    measured=inspect_bundle(site)
    manifest=tmp_path.resolve()/'approved-dependency-pin.json'
    manifest.write_text(json.dumps({key:measured[key] for key in
                    ('kind','site_packages','sha256','distributions')}))
    manifest.chmod(0o600)
    result=checked(dependency=replace(dep,site_packages=site,dependency_manifest=manifest))
    assert result['third_party_pinned'] is True
    assert result['gateway_import'] and result['mail_import']
    assert result['receipt_contract'] and result['import_origins_verified']
    assert result['live_process_verified'] is False and result['service_started'] is False
