"""Content-addressed, immutable helper for an operator-approved ORRERY Mail sender."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from pathlib import Path

from .config import ConfigError
from . import pairing

PACKAGES = (
    ("integrations", "codex_app", "src", "agentstack_codex_app"),
    ("packages", "agentstack_mail", "src", "agentstack_mail"),
)
MANIFEST = "HELPER.json"
WAIT_PRIMITIVE = "bin/agentstack-await-reply"


def _safe_node(path: Path, *, directory: bool, readonly: bool) -> None:
    st = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & (0o222 if readonly else 0o022):
        raise ConfigError("untrusted helper release node")


def _list_modules(package: Path, *, readonly: bool) -> list[Path]:
    _safe_node(package, directory=True, readonly=readonly)
    files = []
    for path in sorted(package.iterdir()):
        if not readonly and path.name == "__pycache__" and path.is_dir() and not path.is_symlink():
            continue
        _safe_node(path, directory=False, readonly=readonly)
        if path.suffix != ".py":
            raise ConfigError("unexpected helper package entry")
        files.append(path)
    if package / "__init__.py" not in files:
        raise ConfigError("missing helper initializer")
    return files


def _digest(files: list[tuple[str, Path]]) -> str:
    h = hashlib.sha256()
    for relative, path in files:
        data = path.read_bytes()
        name = relative.encode()
        h.update(len(name).to_bytes(4, "big") + name + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def _source_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_absolute():
        raise ConfigError("helper source must be absolute")
    files = []
    for parts in PACKAGES:
        for path in _list_modules(root.joinpath(*parts), readonly=False):
            files.append(("/".join((*parts, path.name)), path))
    primitive = root / WAIT_PRIMITIVE
    _safe_node(primitive, directory=False, readonly=False)
    files.append((WAIT_PRIMITIVE, primitive))
    return sorted(files)


def assert_helper_release(root: Path, digest: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", digest or ""):
        raise ConfigError("invalid helper digest")
    _safe_node(root, directory=True, readonly=True)
    if sorted(p.name for p in root.iterdir()) != [MANIFEST, "bin", "integrations", "packages"]:
        raise ConfigError("unexpected helper release layout")
    _safe_node(root / MANIFEST, directory=False, readonly=True)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("digest") != digest:
        raise ConfigError("helper manifest mismatch")
    _safe_node(root / "bin", directory=True, readonly=True)
    if sorted(p.name for p in (root / "bin").iterdir()) != ["agentstack-await-reply"]:
        raise ConfigError("unexpected helper executable layout")
    _safe_node(root / WAIT_PRIMITIVE, directory=False, readonly=True)
    files = [(WAIT_PRIMITIVE, root / WAIT_PRIMITIVE)]
    for parts in PACKAGES:
        for i in range(1, len(parts)):
            directory = root.joinpath(*parts[:i])
            _safe_node(directory, directory=True, readonly=True)
            if len(tuple(directory.iterdir())) != 1:
                raise ConfigError("unexpected helper intermediate directory")
        for path in _list_modules(root.joinpath(*parts), readonly=True):
            files.append(("/".join((*parts, path.name)), path))
    if _digest(sorted(files)) != digest:
        raise ConfigError("helper release digest mismatch")


def verify_helper_release(root: Path, digest: str) -> bool:
    try:
        assert_helper_release(root, digest)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def install_helper_release(source: Path, releases: Path, *, source_commit: str | None = None) -> tuple[Path, str]:
    files = _source_files(source)
    digest = _digest(files)
    pairing._private_dir(releases)
    target = releases / digest[:16]
    if os.path.lexists(target):
        if not verify_helper_release(target, digest):
            raise ConfigError("existing helper release is unsafe")
        return target, digest
    stage = releases / f".{digest[:16]}.{os.getpid()}.tmp"
    if os.path.lexists(stage):
        raise ConfigError("helper staging already exists")
    stage.mkdir(mode=0o700)
    for rel, path in files:
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, dest, follow_symlinks=False)
    (stage / MANIFEST).write_text(json.dumps({
        "digest": digest, "source_commit": source_commit, "created_at": int(time.time()),
    }, sort_keys=True) + "\n", encoding="utf-8")
    for entry in sorted(stage.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        os.chmod(entry, 0o555 if entry.is_dir() else 0o444, follow_symlinks=False)
    os.chmod(stage, 0o555)
    if not verify_helper_release(stage, digest):
        raise ConfigError("helper changed during staging")
    os.rename(stage, target)
    return target, digest


def check_profile(path: Path) -> None:
    if not path.is_absolute():
        raise ConfigError("message profile must be absolute")
    try:
        info = path.lstat()
        if info.st_size > 65536:
            raise ConfigError("message profile is too large")
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ConfigError("message profile must be owner-only (0600), not a symlink")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise ConfigError("message profile cannot be read safely") from None
    fields = {"kind", "name", "agent_id", "project_key", "connection", "peers", "pause_path", "lock_path"}
    if not isinstance(data, dict) or set(data) != fields or data.get("kind") != "orrery-daemon-binding-v2":
        raise ConfigError("message profile must be a canonical daemon v2 binding")
    if not isinstance(data["name"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,127}", data["name"]):
        raise ConfigError("invalid sender name")
    if type(data["agent_id"]) is not int or data["agent_id"] <= 0:
        raise ConfigError("invalid sender Mail ID")
    for key in ("project_key", "connection", "pause_path", "lock_path"):
        if not isinstance(data[key], str) or not Path(data[key]).is_absolute():
            raise ConfigError("invalid message profile path")
    peers = data.get("peers")
    if not isinstance(peers, list) or not 1 <= len(peers) <= 16:
        raise ConfigError("message profile needs an explicitly approved allowlist")
    names, ids = {data["name"]}, {data["agent_id"]}
    for peer in peers:
        if not isinstance(peer, dict) or set(peer) != {"name", "id"} or not isinstance(peer["name"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,127}", peer["name"]) or type(peer["id"]) is not int or peer["id"] < 1:
            raise ConfigError("invalid recipient in message allowlist")
        if peer["name"] in names or peer["id"] in ids:
            raise ConfigError("duplicate sender or recipient in message allowlist")
        names.add(peer["name"])
        ids.add(peer["id"])
