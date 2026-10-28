"""Local-only owner controls: the pairing secret and on-Mac spawn approvals.

The pairing secret is the last path segment of the loopback MCP URL that the
operator gives to Secure MCP Tunnel's ``tunnel-client``. ChatGPT never sees
it, and a browser page or another program cannot guess the endpoint.

A spawn requested from ChatGPT stays pending until the operator approves its
code on this Mac with ``orrery-chatgpt-mcp approve``. Nothing that arrives
over the tunnel can create an approval.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

from .config import PAIRING_RE, ConfigError

KIND = "orrery-chatgpt-mcp-pairing-v1"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(f"[{CODE_ALPHABET}]{{8}}")


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ConfigError(f"{path} is not a directory owned by this user")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)


def _read_private(path: Path, limit: int = 65536) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ConfigError(f"{path} must be a private regular file (0600) owned by this user")
        return fh.read(limit + 1)[:limit]


def _write_private(path: Path, data: dict, *, exclusive: bool) -> None:
    _private_dir(path.parent)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


# -- pairing secret -----------------------------------------------------------

def secret_path(state_dir: Path) -> Path:
    return state_dir / "pairing.json"


def read_secret(state_dir: Path) -> str:
    path = secret_path(state_dir)
    try:
        value = json.loads(_read_private(path, 4096))
    except FileNotFoundError:
        raise ConfigError("no pairing secret yet; run `orrery-chatgpt-mcp pair` once on this Mac") from None
    except (OSError, ValueError) as exc:
        raise ConfigError(f"pairing file is unreadable: {exc}") from None
    if not isinstance(value, dict) or value.get("kind") != KIND or not PAIRING_RE.fullmatch(str(value.get("secret", ""))):
        raise ConfigError("pairing file is malformed; run `orrery-chatgpt-mcp pair --rotate`")
    return value["secret"]


def create_secret(state_dir: Path, *, rotate: bool = False) -> str:
    secret = secrets.token_urlsafe(32)
    assert PAIRING_RE.fullmatch(secret)
    record = {"kind": KIND, "secret": secret, "created": int(time.time())}
    try:
        _write_private(secret_path(state_dir), record, exclusive=not rotate)
    except FileExistsError:
        raise ConfigError("already paired; use `orrery-chatgpt-mcp url` to show the URL or `pair --rotate` to replace it") from None
    return secret


# -- spawn approvals ------------------------------------------------------------

def approvals_dir(state_dir: Path) -> Path:
    return state_dir / "approvals"


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def write_pending(state_dir: Path, code: str, summary: dict, preview_digest: str, expires_at: float) -> None:
    _write_private(approvals_dir(state_dir) / f"pending-{code}.json",
                   {"code": code, "summary": summary, "preview": preview_digest, "expires_at": expires_at}, exclusive=True)


def read_pending(state_dir: Path, code: str) -> dict | None:
    if not CODE_RE.fullmatch(code):
        return None
    try:
        return json.loads(_read_private(approvals_dir(state_dir) / f"pending-{code}.json"))
    except (FileNotFoundError, ValueError):
        return None


def list_pending(state_dir: Path, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    out = []
    try:
        names = sorted(os.listdir(approvals_dir(state_dir)))
    except FileNotFoundError:
        return out
    for name in names:
        m = re.fullmatch(r"pending-([A-Z0-9]{8})\.json", name)
        if m:
            record = read_pending(state_dir, m.group(1))
            if record and record.get("expires_at", 0) > now:
                record["approved"] = is_approved(state_dir, m.group(1), record.get("preview", ""))
                out.append(record)
    return out


def approve(state_dir: Path, code: str, now: float | None = None, *, shown: dict | None = None) -> dict:
    """Approve ``code``. ``shown`` is the record the operator read; the approval
    names the hash of that task text, computed here, not copied from the file."""
    import hashlib

    now = time.time() if now is None else now
    record = read_pending(state_dir, code)
    if record is None:
        raise ConfigError(f"no pending launch with code {code}")
    if shown is not None and shown != record:
        raise ConfigError(f"launch {code} changed while it was displayed; run approve again")
    if record.get("expires_at", 0) <= now:
        raise ConfigError(f"launch {code} expired; ask ChatGPT for a new preview")
    task = (record.get("summary") or {}).get("task")
    if not isinstance(task, str):
        raise ConfigError(f"launch {code} has no task text to approve")
    task_sha = hashlib.sha256(task.encode()).hexdigest()
    _write_private(approvals_dir(state_dir) / f"approved-{code}.json",
                   {"code": code, "preview": record["preview"], "task_sha256": task_sha, "approved_at": int(now)}, exclusive=False)
    return record


def deny(state_dir: Path, code: str) -> bool:
    removed = False
    if CODE_RE.fullmatch(code):
        for prefix in ("pending", "approved"):
            try:
                (approvals_dir(state_dir) / f"{prefix}-{code}.json").unlink()
                removed = True
            except FileNotFoundError:
                pass
    return removed


def is_approved(state_dir: Path, code: str, preview_digest: str, task_sha256: str | None = None) -> bool:
    """True only for an approval of this preview, and (when given) of exactly this task text."""
    if not CODE_RE.fullmatch(code):
        return False
    try:
        record = json.loads(_read_private(approvals_dir(state_dir) / f"approved-{code}.json"))
    except (FileNotFoundError, ValueError, ConfigError):
        return False
    if not isinstance(record, dict) or record.get("preview") != preview_digest:
        return False
    return task_sha256 is None or record.get("task_sha256") == task_sha256


def consume(state_dir: Path, code: str) -> None:
    deny(state_dir, code)
