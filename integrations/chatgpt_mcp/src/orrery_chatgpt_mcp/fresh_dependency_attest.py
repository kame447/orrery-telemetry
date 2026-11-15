"""Read-only attestation of an operator-pinned, immutable Python dependency tree.

This is not a wheel installer, source provenance claim or process-start proof.
The operator approves a complete artifact snapshot outside the MCP protocol.
"""
from __future__ import annotations

from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


class DependencyAttestationError(ValueError):
    """Fixed refusal code; never includes local paths, package data or secrets."""


KIND = 'orrery-mcp-third-party-bundle-v1'
NAME = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?')
VERSION = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._+!-]{0,126}[A-Za-z0-9])?')
SHA256 = re.compile(r'[0-9a-f]{64}')
MAX_FILES = 20000
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 65536  # Private operator pin manifest
MAX_DISTRIBUTION_METADATA_BYTES = 256 * 1024  # Real package METADATA may exceed 64 KiB


def _canonical(path: Path) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('non-canonical path')


def _node(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o222
            or (not directory and info.st_nlink != 1)):
        raise ValueError('untrusted node')


def _hash_file(path: Path, digest: Any) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o222 or info.st_nlink != 1
                or info.st_size > MAX_FILE_BYTES):
            raise ValueError('invalid dependency file')
        digest.update(info.st_mode.to_bytes(4, 'big'))
        digest.update(info.st_size.to_bytes(8, 'big'))
        amount = 0
        while True:
            chunk = os.read(fd, min(262144, MAX_FILE_BYTES - amount + 1))
            if not chunk:
                break
            amount += len(chunk)
            if amount > MAX_FILE_BYTES:
                raise ValueError('oversized dependency file')
            digest.update(chunk)
        if amount != info.st_size or os.fstat(fd).st_mtime_ns != info.st_mtime_ns:
            raise ValueError('changed dependency file')
        return amount
    finally:
        os.close(fd)


def _inventory(root: Path) -> tuple[str, tuple[tuple[str, str], ...]]:
    _canonical(root)
    _node(root, directory=True)
    h = hashlib.sha256()
    count = 0
    size = 0
    metadata: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        _node(directory, directory=True)
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
        for node in entries:
            name = node.name
            if (name in {'__pycache__', '.', '..'} or '\\' in name
                    or name.endswith(('.pth', '.egg-link', '.pyc', '.pyo'))):
                raise ValueError('executable import overlay')
            if not name or '\x00' in name or '\n' in name or '\r' in name:
                raise ValueError('invalid dependency filename')
            relative = node.relative_to(root).as_posix().encode('utf-8')
            info = node.lstat()
            if info.st_uid != os.getuid() or info.st_mode & 0o222:
                raise ValueError('untrusted dependency permissions')
            h.update(len(relative).to_bytes(4, 'big'))
            h.update(relative)
            h.update((b'D' if stat.S_ISDIR(info.st_mode) else b'F'))
            if stat.S_ISDIR(info.st_mode):
                stack.append(node)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise ValueError('linked dependency file')
                count += 1
                if count > MAX_FILES:
                    raise ValueError('dependency file limit')
                size += _hash_file(node, h)
                if size > MAX_TOTAL_BYTES:
                    raise ValueError('dependency byte limit')
                if node.name == 'METADATA' and node.parent.name.endswith('.dist-info'):
                    metadata.append(node)
            else:
                raise ValueError('unexpected dependency node')
    if not metadata:
        raise ValueError('unidentified dependencies')
    packages = {}
    for path in metadata:
        if path.stat().st_size > MAX_DISTRIBUTION_METADATA_BYTES:
            raise ValueError('dependency metadata too large')
        info = Parser().parsestr(path.read_text(encoding='utf-8'), headersonly=True)
        name, version = info.get('Name'), info.get('Version')
        if (not isinstance(name, str) or not NAME.fullmatch(name)
                or not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise ValueError('dependency metadata missing')
        normalized = re.sub(r'[-_.]+', '-', name).lower()
        if normalized in packages:
            raise ValueError('duplicate installed distribution')
        packages[normalized] = version
    if not {'fastmcp', 'mcp'}.issubset(packages):
        raise ValueError('MCP runtime dependencies missing')
    return h.hexdigest(), tuple(sorted(packages.items()))


def inspect(site_packages: Path) -> dict:
    """Read an exact artifact snapshot; does not create or accept a pin."""
    try:
        sha, packages = _inventory(site_packages)
        return {'kind': KIND, 'site_packages': str(site_packages), 'sha256': sha,
                'distributions': [{'name': name, 'version': version} for name, version in packages],
                'file_tree_verified_at_rest': True, 'live_process_verified': False,
                'origin_authenticity_verified': False, 'service_started': False}
    except Exception:
        raise DependencyAttestationError('FRESH_DEPENDENCY_BUNDLE_UNVERIFIED') from None


def _policy(path: Path) -> dict:
    _canonical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > MAX_METADATA_BYTES):
            raise ValueError('invalid pin manifest')
        data = os.read(fd, MAX_METADATA_BYTES + 1)
        if len(data) != info.st_size:
            raise ValueError('changed pin manifest')
    finally:
        os.close(fd)

    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError('duplicate manifest field')
            out[k] = v
        return out
    result = json.loads(data, object_pairs_hook=unique)
    if type(result) is not dict:
        raise ValueError('invalid manifest object')
    return result


def verify(manifest_path: Path, expected_site: Path) -> dict:
    """Check a preapproved private manifest; fail closed on any tree drift."""
    try:
        approved = _policy(manifest_path)
        if (set(approved) != {'kind', 'site_packages', 'sha256', 'distributions'}
                or approved['kind'] != KIND
                or type(approved['site_packages']) is not str
                or approved['site_packages'] != str(expected_site)
                or type(approved['sha256']) is not str
                or not SHA256.fullmatch(approved['sha256'])
                or type(approved['distributions']) is not list):
            raise ValueError('manifest schema')
        actual = inspect(expected_site)
        if any(approved[k] != actual[k] for k in ('kind', 'site_packages', 'sha256', 'distributions')):
            raise ValueError('dependency snapshot mismatch')
        return {'validated': True, 'third_party_pinned_at_rest': True,
                'site_packages_sha256': actual['sha256'],
                'distribution_count': len(actual['distributions']),
                'live_process_verified': False, 'origin_authenticity_verified': False,
                'service_started': False, 'operation': 'offline_dependency_preflight'}
    except Exception:
        raise DependencyAttestationError('FRESH_DEPENDENCY_PIN_REFUSED') from None
