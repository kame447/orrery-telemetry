"""Offline import attestation for a future isolated fresh MCP LaunchAgent.

This is a preflight, not permission to install or launch a service. It checks
immutable first-party packages before import in a separate -I -S interpreter.
Third-party dependency versions and the process that later starts are not pinned.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Callable

from .fresh_release_plan import Plan


class ImportAttestationError(ValueError):
    """Fixed labels; subprocess stderr and local paths never escape."""


def _python_sources(package: Path) -> tuple[Path, ...]:
    try:
        if not package.is_absolute() or package.resolve(strict=True) != package:
            raise ValueError
        node = package.lstat()
        if not stat.S_ISDIR(node.st_mode) or node.st_uid != os.getuid() or node.st_mode & 0o222:
            raise ValueError
        sources = []
        for path in sorted(package.iterdir()):
            info = path.lstat()
            if (path.suffix != '.py' or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o222 or info.st_nlink != 1):
                raise ValueError
            sources.append(path)
        if not sources or not any(path.name == '__init__.py' for path in sources):
            raise ValueError
        return tuple(sources)
    except (OSError, ValueError):
        raise ImportAttestationError('FRESH_MAIL_PACKAGE_UNVERIFIED') from None


def package_digest(package: Path) -> str:
    """Same named-file hash algorithm as the existing gateway release checker."""
    h = hashlib.sha256()
    for path in _python_sources(package):
        # No symlink following. A changed source is detected again inside the child.
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                data = stream.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError
        except (OSError, ValueError):
            raise ImportAttestationError('FRESH_MAIL_PACKAGE_UNVERIFIED') from None
        name = path.name.encode('ascii')
        h.update(len(name).to_bytes(4, 'big') + name + len(data).to_bytes(8, 'big') + data)
    return h.hexdigest()


# Executed by a fresh Python interpreter without importing the gateway or Mail
# code before checking their entire source payload and the stated path roots.
PROBE = r'''
import hashlib, importlib, json, os, pathlib, stat, sys
release, expected_gateway, mail, expected_mail, site = map(pathlib.Path, sys.argv[1:6])
pin_manifest = pathlib.Path(sys.argv[6]) if sys.argv[6] else None
expected_pin_hash = sys.argv[7]
expected_gateway, expected_mail = str(expected_gateway), str(expected_mail)

def fail():
    raise SystemExit(78)

def canonical(path, *, writable=False):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        fail()
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        fail()
    if not writable and info.st_mode & 0o222:
        fail()

def fingerprint(package):
    canonical(package)
    names = sorted(package.iterdir())
    if not names or not (package / '__init__.py') in names:
        fail()
    h = hashlib.sha256()
    for path in names:
        info = path.lstat()
        if path.suffix != '.py' or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o222 or info.st_nlink != 1:
            fail()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(16777217)
        if len(data) > 16777216:
            fail()
        name = path.name.encode('ascii')
        h.update(len(name).to_bytes(4,'big') + name + len(data).to_bytes(8,'big') + data)
    return h.hexdigest()

canonical(release)
canonical(site, writable=True)
if fingerprint(release / 'orrery_chatgpt_mcp') != expected_gateway:
    fail()
if fingerprint(mail) != expected_mail or mail.name != 'agentstack_mail':
    fail()
# Explicit site location, no site initialization, .pth or user directories.
sys.path[:0] = [str(release), str(mail.parent), str(site)]
if pin_manifest is not None:
    try:
        from orrery_chatgpt_mcp.fresh_dependency_attest import verify
        proof = verify(pin_manifest, site)
        if not proof['third_party_pinned_at_rest'] or proof['site_packages_sha256'] != expected_pin_hash:
            fail()
    except Exception:
        fail()
try:
    gateway = importlib.import_module('orrery_chatgpt_mcp.fresh_server')
    client = importlib.import_module('agentstack_mail.fresh_client')
    receipts = importlib.import_module('agentstack_mail.fresh_message_receipts')
    fastmcp = importlib.import_module('fastmcp')
    expected = {
        gateway: release / 'orrery_chatgpt_mcp' / 'fresh_server.py',
        client: mail / 'fresh_client.py',
        receipts: mail / 'fresh_message_receipts.py',
    }
    for module, path in expected.items():
        if pathlib.Path(module.__file__).resolve(strict=True) != path:
            fail()
    if ('get_message_receipt' not in client.FIELDS
            or not callable(getattr(client.FreshBoundClient, 'receipt', None))
            or not callable(getattr(gateway, 'main', None))
            or not callable(getattr(fastmcp, 'Client', None))):
        fail()
    if pin_manifest is not None:
        # A verified bundle alone does not prove imports came from that bundle.
        for module_name in ('fastmcp', 'mcp', 'sqlalchemy', 'sqlmodel', 'pydantic'):
            imported = importlib.import_module(module_name)
            source = pathlib.Path(imported.__file__).resolve(strict=True)
            if not source.is_relative_to(site):
                fail()
except Exception:
    fail()
if pin_manifest is not None:
    try:
        # A second complete snapshot after imports catches mutation during loading.
        proof = verify(pin_manifest, site)
        if not proof['third_party_pinned_at_rest'] or proof['site_packages_sha256'] != expected_pin_hash:
            fail()
    except Exception:
        fail()
print(json.dumps({'gateway_import':True,'mail_import':True,'receipt_contract':True,
                  'import_origins_verified':True,'third_party_pinned':pin_manifest is not None}))
'''


@dataclass(frozen=True)
class ImportPlan:
    """Source roots are host-only, never accepted in MCP tool arguments."""
    mail_package: Path
    mail_digest: str
    site_packages: Path
    dependency_manifest: Path | None = None


def check(plan: Plan, dependency: ImportPlan, *, verify_release: Callable,
          read_release: Callable, runner: Callable = subprocess.run) -> dict:
    """Run only a separate, no-network, no-service import check."""
    try:
        from .fresh_release_plan import inspect
        if inspect(plan, verify_release, read_release).get('validated') is not True:
            raise ValueError
        if (type(dependency.mail_digest) is not str or not re.fullmatch('[a-f0-9]{64}', dependency.mail_digest)
                or dependency.mail_package.name != 'agentstack_mail'
                or package_digest(dependency.mail_package) != dependency.mail_digest):
            raise ValueError
        site = dependency.site_packages
        pin = None
        if dependency.dependency_manifest is not None:
            # The operator's manifest is fixed to this selected site by the
            # read-only verifier; neither is accepted from model tool input.
            from .fresh_dependency_attest import verify as verify_dependency
            pin = verify_dependency(dependency.dependency_manifest, site)['site_packages_sha256']
        if (not site.is_absolute() or site.resolve(strict=True) != site
                or not site.is_dir() or site.is_symlink()):
            raise ValueError
        cmd = [str(plan.python), '-I', '-S', '-B', '-c', PROBE, str(plan.release_dir),
               plan.release_digest, str(dependency.mail_package), dependency.mail_digest, str(site),
               str(dependency.dependency_manifest) if pin is not None else '', pin or '']
        # Keep host credentials, service URLs, user-site controls and provider
        # session data entirely out of the interpreter.
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'PYTHONDONTWRITEBYTECODE': '1'}
        done = runner(cmd, capture_output=True, text=True, timeout=30, env=env)
        if done.returncode != 0 or len(done.stdout) > 2048:
            raise ValueError
        value = json.loads(done.stdout)
        if value != {'gateway_import': True, 'mail_import': True, 'receipt_contract': True,
                     'import_origins_verified': True, 'third_party_pinned': pin is not None}:
            raise ValueError
        if pin is not None:
            # Do not return a successful pinned proof if the operator-approved
            # tree changes between child verification and the caller's result.
            if verify_dependency(dependency.dependency_manifest, site)['site_packages_sha256'] != pin:
                raise ValueError
        return {'validated': True, 'gateway_import': True, 'mail_import': True,
                'receipt_contract': True, 'import_origins_verified': True,
                'third_party_pinned': pin is not None, 'live_process_verified': False,
                'service_started': False, 'operation': 'offline_import_preflight'}
    except Exception:
        raise ImportAttestationError('FRESH_IMPORT_ATTESTATION_REFUSED') from None
