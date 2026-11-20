"""Unapproved Python siblings never execute in the attested interpreter."""
from pathlib import Path
import stat
import subprocess

import pytest

from orrery_chatgpt_mcp.fresh_import_attest import ImportAttestationError
from test_fresh_import_attest import case
from test_fresh_import_dependency_pin import pinned


def add_unapproved_email(root: Path, marker: Path) -> None:
    original_mode = stat.S_IMODE(root.stat().st_mode)
    root.chmod(0o700)
    try:
        package = root / 'email'
        package.mkdir(mode=0o700)
        source = package / '__init__.py'
        source.write_text(
            'from pathlib import Path\n'
            f'Path({str(marker)!r}).write_text("unapproved import")\n'
            'raise RuntimeError("unapproved sibling executed")\n'
        )
        source.chmod(0o444)
        package.chmod(0o555)
    finally:
        root.chmod(original_mode)


@pytest.mark.parametrize('location', ['mail_parent', 'gateway_release'])
def test_unverified_sibling_is_not_an_import_source(tmp_path, location):
    plan, dependency, checked = case(tmp_path)
    root = dependency.mail_package.parent if location == 'mail_parent' else plan.release_dir
    marker = tmp_path / 'unapproved-import.txt'
    add_unapproved_email(root, marker)
    if location == 'gateway_release':
        with pytest.raises(ImportAttestationError):
            checked()
        assert not marker.exists(), 'unapproved release module executed'
    else:
        try:
            result = checked()
        finally:
            assert not marker.exists(), 'unapproved sibling module executed'
        assert result['validated'] is True


def test_dependency_verifier_runs_before_unapproved_site_can_import(tmp_path):
    _, dependency, checked, site, _ = pinned(tmp_path)
    marker = tmp_path / 'unapproved-import.txt'

    def changed_after_host_verification(arguments, **kwargs):
        add_unapproved_email(site, marker)
        return subprocess.run(arguments, **kwargs)

    with pytest.raises(ImportAttestationError):
        checked(dependency=dependency, runner=changed_after_host_verification)
    assert not marker.exists(), 'unapproved dependency executed before verification'
