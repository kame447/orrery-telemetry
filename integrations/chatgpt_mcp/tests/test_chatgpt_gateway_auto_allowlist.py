"""Isolated auto-admission policy and v2 profile CAS tests (no live Mail)."""
from __future__ import annotations

import copy
import json
import os
import stat

import pytest

import chatgpt_gateway_support  # noqa: F401 - register gateway source path
from orrery_chatgpt_mcp.auto_allowlist import AdmissionError, append_verified_peer, snapshot_profile, verify_mcp_spawn

INSTANCE = "7b45b61c-3efc-4197-a00f-e9d21bb1da31"
PROJECT = "/opt/test/project"


def observations():
    binding = dict(child_name="QuartzTesla", mail_agent_id=202,
                   mail_instance_id=INSTANCE, mail_project_key=PROJECT)
    return dict(
        enabled=True,
        ledger=dict(mcp_authorized=True, state="accepted", name="QuartzTesla",
                    preview_id="a" * 64, result=dict(binding)),
        launch=dict(name="QuartzTesla", state="ready", result=dict(binding)),
        sender=dict(instance=INSTANCE, project_key=PROJECT, name="ChatRelay", agent_id=101),
        peer=dict(name="QuartzTesla", id=202, retired=False,
                  project_id=3, registered_at="2026-11-23T12:00:00Z"),
    )


def test_happy_path_requires_four_attestations():
    candidate = verify_mcp_spawn(**observations())
    assert candidate.name == "QuartzTesla" and candidate.agent_id == 202
    assert candidate.sender_name == "ChatRelay" and candidate.mail_instance == INSTANCE


@pytest.mark.parametrize("patch,reason", [
    ({"enabled": False}, "opt_in_required"),
    ({"ledger.mcp_authorized": False}, "no_mcp_spawn_proof"),
    ({"ledger.state": "unknown"}, "no_mcp_spawn_proof"),
    ({"ledger.preview_id": "x"}, "no_mcp_spawn_proof"),
    ({"launch.state": "launching"}, "not_ready"),
    ({"launch.state": "failed"}, "not_ready"),
    ({"ledger.result.mail_agent_id": None}, "unattested_spawn"),
    ({"ledger.result.mail_agent_id": True}, "unattested_spawn"),
    ({"ledger.result.mail_agent_id": 999}, "spawn_binding_changed"),
    ({"launch.result.mail_agent_id": 888}, "spawn_binding_changed"),
    ({"launch.result.mail_instance_id": "different"}, "spawn_binding_changed"),
    ({"launch.name": "RenamedAgent"}, "spawn_binding_changed"),
    ({"sender.instance": "other-instance"}, "sender_binding_changed"),
    ({"sender.project_key": "/different"}, "sender_binding_changed"),
    ({"peer.id": 303}, "peer_binding_changed"),
    ({"peer.retired": True}, "peer_binding_changed"),
    ({"peer.project_id": -1}, "peer_binding_changed"),
    ({"peer.registered_at": ""}, "peer_binding_changed"),
    ({"peer.name": "DifferentPeer"}, "peer_binding_changed"),
])
def test_fail_closed(patch, reason):
    args = copy.deepcopy(observations())
    for dotted, val in patch.items():
        keys = dotted.split(".")
        obj = args
        for key in keys[:-1]:
            obj = obj[key]
        obj[keys[-1]] = val
    with pytest.raises(AdmissionError) as caught:
        verify_mcp_spawn(**args)
    assert caught.value.code == reason


@pytest.fixture
def profile(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    payload = {
        "kind": "orrery-daemon-binding-v2", "name": "ChatRelay", "agent_id": 101,
        "project_key": PROJECT, "connection": str(root / "conn.json"),
        "peers": [{"name": "ExistingPeer", "id": 102}],
        "pause_path": str(root / "pause"), "lock_path": str(root / "daemon.lock"),
    }
    dest = root / "sender.json"
    dest.write_text(json.dumps(payload), encoding="utf-8")
    dest.chmod(0o600)
    return dest


def test_atomic_append_preserves_existing_peer_and_mode(profile):
    candidate = verify_mcp_spawn(**observations())
    digest = snapshot_profile(profile)
    assert append_verified_peer(profile, candidate, expected_sha256=digest) == "registered"
    saved = json.loads(profile.read_text())
    assert saved["peers"] == [{"name": "ExistingPeer", "id": 102},
                               {"name": "QuartzTesla", "id": 202}]
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
    assert append_verified_peer(profile, candidate, expected_sha256=snapshot_profile(profile)) == "already_registered"


def test_stale_snapshot_does_not_overwrite(profile):
    candidate = verify_mcp_spawn(**observations())
    before = snapshot_profile(profile)
    data = json.loads(profile.read_text())
    data["peers"].append({"name": "OtherPeer", "id": 103})
    profile.write_text(json.dumps(data))
    profile.chmod(0o600)
    with pytest.raises(AdmissionError, match="profile_changed"):
        append_verified_peer(profile, candidate, expected_sha256=before)
    assert json.loads(profile.read_text())["peers"][-1]["name"] == "OtherPeer"


def test_full_does_not_evict_existing(profile):
    obj = json.loads(profile.read_text())
    obj["peers"] = [{"name": f"Agent{n}", "id": n + 300} for n in range(16)]
    profile.write_text(json.dumps(obj))
    profile.chmod(0o600)
    before = profile.read_bytes()
    with pytest.raises(AdmissionError, match="allowlist_full"):
        append_verified_peer(profile, verify_mcp_spawn(**observations()),
                             expected_sha256=snapshot_profile(profile))
    assert profile.read_bytes() == before


def test_id_and_name_reuse_denied(profile):
    obj = json.loads(profile.read_text())
    candidate = verify_mcp_spawn(**observations())
    for peer in ({"name": candidate.name, "id": 901}, {"name": "OtherAgent", "id": candidate.agent_id}):
        obj["peers"] = [peer]
        profile.write_text(json.dumps(obj))
        profile.chmod(0o600)
        with pytest.raises(AdmissionError, match="peer_name_or_id_reused"):
            append_verified_peer(profile, candidate, expected_sha256=snapshot_profile(profile))


def test_symlink_and_world_readable_rejected(profile):
    candidate = verify_mcp_spawn(**observations())
    link = profile.parent / "link.json"
    link.symlink_to(profile)
    with pytest.raises(AdmissionError, match="unsafe_profile_file"):
        snapshot_profile(link)
    profile.chmod(0o644)
    with pytest.raises(AdmissionError, match="unsafe_profile_file"):
        append_verified_peer(profile, candidate, expected_sha256="a" * 64)


def test_lock_contention_denies_without_change(profile):
    import fcntl
    lock_path = profile.parent / "daemon.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = profile.read_bytes()
        with pytest.raises(AdmissionError, match="sender_busy"):
            append_verified_peer(profile, verify_mcp_spawn(**observations()),
                                 expected_sha256=snapshot_profile(profile))
        assert profile.read_bytes() == original
    finally:
        os.close(fd)


def test_sender_binding_change_refused(profile):
    obj = json.loads(profile.read_text())
    obj["agent_id"] += 1
    profile.write_text(json.dumps(obj))
    profile.chmod(0o600)
    with pytest.raises(AdmissionError, match="sender_profile_changed"):
        append_verified_peer(profile, verify_mcp_spawn(**observations()),
                             expected_sha256=snapshot_profile(profile))
