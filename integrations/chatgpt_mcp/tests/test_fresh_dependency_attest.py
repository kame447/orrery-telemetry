"""Synthetic immutable third-party bundles; no installed application touched."""
import json
import os
from pathlib import Path

import pytest

from orrery_chatgpt_mcp.fresh_dependency_attest import (
    DependencyAttestationError, inspect, verify,
)


def fixture(tmp_path):
    site = tmp_path.resolve() / 'bundle'
    site.mkdir(mode=0o700)
    for name, version in [('fastmcp', '2.13.0.2'), ('mcp', '1.29.1')]:
        p = site / name
        p.mkdir()
        (p / '__init__.py').write_text('version = ' + repr(version) + '\n')
        d = site / f'{name}-{version}.dist-info'
        d.mkdir()
        (d / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n')
    freeze(site)
    manifest = tmp_path.resolve() / 'operator-pin.json'
    data = inspect(site)
    manifest.write_text(json.dumps({k: data[k] for k in ('kind', 'site_packages', 'sha256', 'distributions')}))
    manifest.chmod(0o600)
    return site, manifest


def freeze(root):
    for path in root.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def unfreeze(root):
    root.chmod(0o700)
    for path in root.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)


def test_immutable_operator_approved_tree(tmp_path):
    site, manifest = fixture(tmp_path)
    value = verify(manifest, site)
    assert value['third_party_pinned_at_rest'] is True
    assert value['live_process_verified'] is False
    assert value['origin_authenticity_verified'] is False
    assert value['service_started'] is False
    assert value['distribution_count'] == 2
    assert verify(manifest, site) == value


@pytest.mark.parametrize('tamper', [
    'bytes', 'new_file', 'pth', 'pyc', 'cache', 'symlink', 'hardlink',
    'world_write', 'group_write', 'directory_write', 'metadata_version', 'extra_dist',
])
def test_mutation_or_import_overlay_is_refused(tmp_path, tamper):
    site, manifest = fixture(tmp_path)
    unfreeze(site)
    file = site / 'mcp' / '__init__.py'
    if tamper == 'bytes':
        file.write_text('hijacked\n')
    elif tamper == 'new_file':
        (site / 'mcp' / 'new.py').write_text('x')
    elif tamper == 'pth':
        (site / 'startup.pth').write_text('import malicious')
    elif tamper == 'pyc':
        (site / 'mcp' / 'module.pyc').write_bytes(b'bad')
    elif tamper == 'cache':
        (site / 'mcp' / '__pycache__').mkdir()
    elif tamper == 'symlink':
        (site / 'mcp' / 'unexpected.py').symlink_to(file)
    elif tamper == 'hardlink':
        os.link(file, site / 'mcp' / 'other.py')
    elif tamper == 'world_write':
        file.chmod(0o666)
    elif tamper == 'group_write':
        file.chmod(0o664)
    elif tamper == 'directory_write':
        (site / 'mcp').chmod(0o777)
    elif tamper == 'metadata_version':
        (site / 'mcp-1.29.1.dist-info' / 'METADATA').write_text('Name: mcp\nVersion: 1.30\n')
    elif tamper == 'extra_dist':
        d = site / 'MCP-1.29.1.dist-info'
        d.mkdir()
        (d / 'METADATA').write_text('Name: MCP\nVersion: 1.29.1\n')
    if tamper not in ('world_write', 'group_write', 'directory_write'):
        freeze(site)
    else:
        for path in site.rglob('*'):
            if path != file and (tamper != 'directory_write' or path != site / 'mcp') and not path.is_symlink():
                path.chmod(0o555 if path.is_dir() else 0o444)
        site.chmod(0o555)
    with pytest.raises(DependencyAttestationError):
        verify(manifest, site)
    unfreeze(site)


@pytest.mark.parametrize('variant', [
    'wrong_sha', 'wrong_location', 'extra_field', 'wrong_version',
    'duplicate_field', 'symlink_manifest', 'insecure_manifest', 'hardlink_manifest',
])
def test_unapproved_manifest_refused(tmp_path, variant):
    site, manifest = fixture(tmp_path)
    payload = json.loads(manifest.read_text())
    if variant == 'wrong_sha':
        payload['sha256'] = 'f' * 64
    elif variant == 'wrong_location':
        payload['site_packages'] = '/not-authorized'
    elif variant == 'extra_field':
        payload['auth'] = 'unsafe'
    elif variant == 'wrong_version':
        payload['distributions'][0]['version'] = 'changed'
    elif variant == 'duplicate_field':
        manifest.write_text('{"kind":"a","kind":"b"}')
    elif variant == 'symlink_manifest':
        original = manifest.with_suffix('.other')
        manifest.rename(original)
        manifest.symlink_to(original)
    elif variant == 'insecure_manifest':
        manifest.chmod(0o644)
    elif variant == 'hardlink_manifest':
        os.link(manifest, manifest.with_suffix('.another'))
    if variant in {'wrong_sha', 'wrong_location', 'extra_field', 'wrong_version'}:
        manifest.write_text(json.dumps(payload))
    with pytest.raises(DependencyAttestationError):
        verify(manifest, site)


def test_no_exec_or_write_during_snapshot(tmp_path):
    site, manifest = fixture(tmp_path)
    before = {(p.relative_to(site), p.stat().st_mtime_ns) for p in site.rglob('*')}
    assert inspect(site)['sha256'] == verify(manifest, site)['site_packages_sha256']
    after = {(p.relative_to(site), p.stat().st_mtime_ns) for p in site.rglob('*')}
    assert before == after


def test_empty_and_noncanonical_refused(tmp_path):
    site = tmp_path.resolve() / 'empty'
    site.mkdir(mode=0o555)
    with pytest.raises(DependencyAttestationError):
        inspect(site)
    with pytest.raises(DependencyAttestationError):
        inspect(Path('relative/site'))
