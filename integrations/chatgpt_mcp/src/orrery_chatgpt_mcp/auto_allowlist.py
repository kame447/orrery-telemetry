"""Fail-closed admission and atomic profile update for MCP-spawned peers.

No gateway entry point calls this module yet. In particular, this module does not
turn on auto-authorization. Only a caller holding a trusted, durable MCP spawn
receipt and a fresh, helper-verified Mail observation may use it.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import tempfile
from typing import Mapping

_MAX_PROFILE_BYTES = 65536
_MAX_PEERS = 16
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,127}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class AdmissionError(ValueError):
    """Fixed non-secret reason for refusing an automatic recipient addition."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Candidate:
    name: str
    agent_id: int
    mail_instance: str
    project_key: str
    sender_name: str
    sender_id: int
    registered_at: str


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


def verify_mcp_spawn(*, enabled: bool, ledger: Mapping, launch: Mapping,
                     sender: Mapping, peer: Mapping) -> Candidate:
    """Return a pin only when four independent trusted observations agree.

    'ledger' must originate in the gateway's private, idempotent spawn ledger,
    never from an MCP argument. 'launch' is the ready Dashboard record; 'peer'
    must be read using the canonical Mail helper with a temporary candidate pin.
    Neither a name-only launch nor a legacy status without Mail IDs qualifies.
    """
    if enabled is not True:
        raise AdmissionError("opt_in_required")
    if not all(isinstance(value, Mapping) for value in (ledger, launch, sender, peer)):
        raise AdmissionError("invalid_attestation")
    if (ledger.get("mcp_authorized") is not True or ledger.get("state") != "accepted"
            or not isinstance(ledger.get("preview_id"), str)
            or not _HEX64.fullmatch(ledger["preview_id"])):
        raise AdmissionError("no_mcp_spawn_proof")
    result = ledger.get("result")
    observed = launch.get("result")
    if not isinstance(result, dict) or not isinstance(observed, dict) or launch.get("state") != "ready":
        raise AdmissionError("not_ready")
    name = ledger.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise AdmissionError("invalid_identity")
    agent_id = result.get("mail_agent_id")
    instance = result.get("mail_instance_id")
    project = result.get("mail_project_key")
    provider = result.get("provider")
    if provider not in ("claude", "codex"):
        raise AdmissionError("unattested_spawn")
    if (not _positive_id(agent_id) or not isinstance(instance, str) or not instance
            or not isinstance(project, str) or not Path(project).is_absolute()):
        raise AdmissionError("unattested_spawn")
    if not all((row.get("child_name") == name and row.get("mail_agent_id") == agent_id
                and row.get("provider") == provider and row.get("mail_instance_id") == instance
                and row.get("mail_project_key") == project)
               for row in (result, observed)) or launch.get("name") != name:
        raise AdmissionError("spawn_binding_changed")
    if (sender.get("instance") != instance or sender.get("project_key") != project
            or not isinstance(sender.get("name"), str) or not _NAME.fullmatch(sender["name"])
            or not _positive_id(sender.get("agent_id"))):
        raise AdmissionError("sender_binding_changed")
    if (peer.get("name") != name or peer.get("id") != agent_id
            or peer.get("program") != {"claude": "claude-code", "codex": "codex"}[provider]
            or peer.get("retired") is not False
            or not _positive_id(peer.get("project_id"))
            or not isinstance(peer.get("registered_at"), str)
            or not peer.get("registered_at")):
        raise AdmissionError("peer_binding_changed")
    return Candidate(name=name, agent_id=agent_id, mail_instance=instance,
                     project_key=project, sender_name=sender["name"],
                     sender_id=sender["agent_id"], registered_at=peer["registered_at"])


def _private_regular(path: Path) -> bytes:
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or parent.st_mode & 0o022):
        raise AdmissionError("unsafe_profile_parent")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AdmissionError("unsafe_profile_file") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1
                or info.st_size > _MAX_PROFILE_BYTES):
            raise AdmissionError("unsafe_profile_file")
        data = os.read(fd, _MAX_PROFILE_BYTES + 1)
        if len(data) > _MAX_PROFILE_BYTES:
            raise AdmissionError("unsafe_profile_file")
        return data
    finally:
        os.close(fd)


def snapshot_profile(profile_path: Path) -> str:
    return hashlib.sha256(_private_regular(Path(profile_path))).hexdigest()


def append_verified_peer(profile_path: Path, candidate: Candidate, *, expected_sha256: str) -> str:
    """Compare-and-swap an operator v2 allowlist, never replacing an old peer.

    The exact snapshot must have been inspected and the candidate already
    verified against canonical Mail. Contention or a changed profile refuses
    the operation; the caller must re-verify rather than silently retry.
    The existing daemon lock excludes concurrent sessions during replacement.
    """
    profile_path = Path(profile_path)
    if (not isinstance(candidate, Candidate)
            or not isinstance(candidate.name, str) or not _NAME.fullmatch(candidate.name)
            or not _positive_id(candidate.agent_id)
            or not isinstance(candidate.sender_name, str) or not _NAME.fullmatch(candidate.sender_name)
            or not _positive_id(candidate.sender_id)):
        raise AdmissionError("invalid_candidate")
    if not isinstance(expected_sha256, str) or not _HEX64.fullmatch(expected_sha256):
        raise AdmissionError("invalid_snapshot")
    initial = _private_regular(profile_path)
    if hashlib.sha256(initial).hexdigest() != expected_sha256:
        raise AdmissionError("profile_changed")
    try:
        current = json.loads(initial)
    except (ValueError, UnicodeError) as exc:
        raise AdmissionError("invalid_profile") from exc
    expected_keys = {"kind", "name", "agent_id", "project_key", "connection", "peers", "pause_path", "lock_path"}
    if (not isinstance(current, dict) or set(current) != expected_keys
            or current.get("kind") != "orrery-daemon-binding-v2"
            or current.get("name") != candidate.sender_name
            or current.get("agent_id") != candidate.sender_id
            or current.get("project_key") != candidate.project_key):
        raise AdmissionError("sender_profile_changed")
    peers = current.get("peers")
    if not isinstance(peers, list) or not 1 <= len(peers) <= _MAX_PEERS:
        raise AdmissionError("invalid_profile")
    known_names = set()
    known_ids = set()
    for peer in peers:
        if (not isinstance(peer, dict) or set(peer) != {"name", "id"}
                or not isinstance(peer["name"], str) or not _NAME.fullmatch(peer["name"])
                or not _positive_id(peer["id"]) or peer["name"] in known_names
                or peer["id"] in known_ids or peer["name"] == current["name"]
                or peer["id"] == current["agent_id"]):
            raise AdmissionError("invalid_profile")
        known_names.add(peer["name"])
        known_ids.add(peer["id"])
    if candidate.name == current["name"] or candidate.agent_id == current["agent_id"]:
        raise AdmissionError("sender_is_recipient")
    already_pinned = any(p["name"] == candidate.name and p["id"] == candidate.agent_id for p in peers)
    if not already_pinned and (candidate.name in known_names or candidate.agent_id in known_ids):
        raise AdmissionError("peer_name_or_id_reused")
    if not already_pinned and len(peers) >= _MAX_PEERS:
        raise AdmissionError("allowlist_full")
    lock_path = Path(current["lock_path"])
    if not lock_path.is_absolute():
        raise AdmissionError("unsafe_lock_path")
    parent = lock_path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077):
        raise AdmissionError("unsafe_lock_path")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AdmissionError("unsafe_lock_path")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdmissionError("sender_busy") from exc
        if hashlib.sha256(_private_regular(profile_path)).hexdigest() != expected_sha256:
            raise AdmissionError("profile_changed")
        if already_pinned:
            return "already_registered"
        updated = dict(current)
        updated["peers"] = [*peers, {"name": candidate.name, "id": candidate.agent_id}]
        payload = (json.dumps(updated, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        if len(payload) > _MAX_PROFILE_BYTES:
            raise AdmissionError("profile_too_large")
        fd, temporary = tempfile.mkstemp(prefix=".mcp-allowlist-", dir=profile_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, profile_path)
            directory = os.open(profile_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        return "registered"
    finally:
        os.close(lock_fd)
