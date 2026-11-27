"""Isolated auto-admission policy and v2 profile CAS tests (no live Mail)."""
from __future__ import annotations

import copy
import json
import os
import stat
from pathlib import Path

import pytest

import chatgpt_gateway_support  # noqa: F401 - register gateway source path
from orrery_chatgpt_mcp.auto_allowlist import (AdmissionError, admit_mcp_spawn,
    append_verified_peer, observe_unlisted_peer, snapshot_profile, verify_mcp_spawn)

INSTANCE = "7b45b61c-3efc-4197-a00f-e9d21bb1da31"
PROJECT = "/opt/test/project"
REGISTERED = "2026-11-23T12:00:00Z"


def observations():
    binding = dict(child_name="QuartzTesla", mail_agent_id=202, provider="claude",
                   mail_instance_id=INSTANCE, mail_project_key=PROJECT,
                   mail_project_id=3, mail_registered_at=REGISTERED)
    return dict(
        enabled=True,
        ledger=dict(mcp_authorized=True, state="accepted", name="QuartzTesla",
                    preview_id="a" * 64, result=dict(binding)),
        launch=dict(name="QuartzTesla", state="ready", result=dict(binding)),
        sender=dict(instance=INSTANCE, project_key=PROJECT, project_id=3,
                    name="ChatRelay", agent_id=101),
        peer=dict(name="QuartzTesla", id=202, program="claude-code", retired=False,
                  project_id=3, registered_at=REGISTERED),
    )


def test_happy_path_requires_four_attestations():
    candidate = verify_mcp_spawn(**observations())
    assert candidate.name == "QuartzTesla" and candidate.agent_id == 202
    assert candidate.sender_name == "ChatRelay" and candidate.mail_instance == INSTANCE
    assert candidate.project_id == 3 and candidate.registered_at == REGISTERED


def test_codex_uses_actual_mail_program():
    args = observations()
    for value in (args["ledger"]["result"], args["launch"]["result"]):
        value["provider"] = "codex"
    args["peer"]["program"] = "codex-cli"
    assert verify_mcp_spawn(**args).agent_id == 202
    args["peer"]["program"] = "codex"
    with pytest.raises(AdmissionError) as caught:
        verify_mcp_spawn(**args)
    assert caught.value.code == "peer_binding_changed"


@pytest.mark.parametrize("patch,reason", [
    ({"enabled": False}, "opt_in_required"),
    ({"ledger.mcp_authorized": False}, "no_mcp_spawn_proof"),
    ({"ledger.state": "unknown"}, "no_mcp_spawn_proof"),
    ({"ledger.preview_id": "x"}, "no_mcp_spawn_proof"),
    ({"launch.state": "launching"}, "not_ready"),
    ({"launch.state": "failed"}, "not_ready"),
    ({"ledger.result.mail_agent_id": None}, "unattested_spawn"),
    ({"ledger.result.mail_agent_id": True}, "unattested_spawn"),
    ({"ledger.result.mail_instance_id": "not-a-uuid"}, "unattested_spawn"),
    ({"ledger.result.mail_project_id": True}, "unattested_spawn"),
    ({"ledger.result.mail_project_id": 0}, "unattested_spawn"),
    ({"ledger.result.mail_registered_at": ""}, "unattested_spawn"),
    ({"ledger.result.mail_agent_id": 999}, "spawn_binding_changed"),
    ({"launch.result.mail_agent_id": 888}, "spawn_binding_changed"),
    ({"launch.result.mail_agent_id": 202.0}, "spawn_binding_changed"),
    ({"launch.result.mail_project_id": 3.0}, "spawn_binding_changed"),
    ({"launch.result.mail_instance_id": "different"}, "spawn_binding_changed"),
    ({"launch.result.mail_project_id": 4}, "spawn_binding_changed"),
    ({"launch.result.mail_registered_at": "other-generation"}, "spawn_binding_changed"),
    ({"launch.name": "RenamedAgent"}, "spawn_binding_changed"),
    ({"launch.result.provider": "codex"}, "spawn_binding_changed"),
    ({"peer.program": "codex"}, "peer_binding_changed"),
    ({"sender.instance": "other-instance"}, "sender_binding_changed"),
    ({"sender.project_key": "/different"}, "sender_binding_changed"),
    ({"sender.project_id": 999}, "sender_binding_changed"),
    ({"sender.project_id": True}, "sender_binding_changed"),
    ({"peer.id": 303}, "peer_binding_changed"),
    ({"peer.id": 202.0}, "peer_binding_changed"),
    ({"peer.retired": True}, "peer_binding_changed"),
    ({"peer.project_id": -1}, "peer_binding_changed"),
    ({"peer.project_id": 4}, "peer_binding_changed"),
    ({"peer.registered_at": "other-generation"}, "peer_binding_changed"),
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


def test_parallel_admissions_never_lose_or_duplicate_peers(profile):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Barrier

    a = verify_mcp_spawn(**observations())
    b = replace(a, name="NovaBohr", agent_id=203)
    digest = snapshot_profile(profile)
    barrier = Barrier(2)

    def attempt(candidate):
        barrier.wait()
        try:
            return append_verified_peer(profile, candidate, expected_sha256=digest)
        except AdmissionError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (a, b)))
    assert results.count("registered") == 1
    assert all(x in {"registered", "sender_busy", "profile_changed"} for x in results)
    saved = json.loads(profile.read_text())
    assert len(saved["peers"]) == 2
    assert saved["peers"][0] == {"name": "ExistingPeer", "id": 102}


def test_snapshot_unlinked_by_concurrent_atomic_append_is_changed(profile, monkeypatch):
    from dataclasses import replace
    from orrery_chatgpt_mcp import auto_allowlist

    winner = verify_mcp_spawn(**observations())
    loser = replace(winner, name="NovaBohr", agent_id=203)
    digest = snapshot_profile(profile)
    original_open = os.open
    replaced = False

    def open_then_replace(path, flags, *args, **kwargs):
        nonlocal replaced
        fd = original_open(path, flags, *args, **kwargs)
        if not replaced and Path(path) == profile and flags & os.O_ACCMODE == os.O_RDONLY:
            replaced = True
            try:
                assert append_verified_peer(profile, winner, expected_sha256=digest) == "registered"
                assert os.fstat(fd).st_nlink == 0
            except BaseException:
                os.close(fd)
                raise
        return fd

    monkeypatch.setattr(auto_allowlist.os, "open", open_then_replace)
    with pytest.raises(AdmissionError, match="profile_changed"):
        append_verified_peer(profile, loser, expected_sha256=digest)
    assert replaced
    assert json.loads(profile.read_text())["peers"] == [
        {"name": "ExistingPeer", "id": 102}, {"name": winner.name, "id": winner.agent_id}]


def test_hardlinked_profile_remains_unsafe_for_compare_and_swap(profile):
    digest = snapshot_profile(profile)
    os.link(profile, profile.parent / "alias.json")
    before = profile.read_bytes()
    with pytest.raises(AdmissionError, match="unsafe_profile_file"):
        append_verified_peer(profile, verify_mcp_spawn(**observations()), expected_sha256=digest)
    assert profile.read_bytes() == before


def test_invalid_candidate_never_changes_profile(profile):
    from dataclasses import replace
    good = verify_mcp_spawn(**observations())
    old = profile.read_bytes()
    with pytest.raises(AdmissionError, match="invalid_candidate"):
        append_verified_peer(profile, replace(good, agent_id=True),
                             expected_sha256=snapshot_profile(profile))
    assert profile.read_bytes() == old


def test_ephemeral_candidate_pin_does_not_modify_operator_allowlist(profile, monkeypatch):
    from contextlib import contextmanager
    from orrery_chatgpt_mcp import messaging

    original = profile.read_bytes()
    seen = []

    class Sender:
        def __init__(self, temp_path, helper_root):
            assert temp_path != profile
            assert helper_root == profile.parent
            assert stat.S_IMODE(temp_path.stat().st_mode) == 0o600
            value = json.loads(temp_path.read_text())
            assert value["peers"] == [
                {"name": "ExistingPeer", "id": 102},
                {"name": "QuartzTesla", "id": 202},
            ]
            seen.append(temp_path)

        @contextmanager
        def session(self):
            class Runtime:
                def identity(self):
                    return {"instance": INSTANCE, "project_key": PROJECT,
                            "name": "ChatRelay", "agent_id": 101}

                def lookup(self, name):
                    assert name == "QuartzTesla"
                    return observations()["peer"]
            yield Runtime()

    monkeypatch.setattr(messaging, "DaemonSender", Sender)
    sender, peer = observe_unlisted_peer(
        profile, profile.parent, name="QuartzTesla", agent_id=202,
        expected_sha256=snapshot_profile(profile))
    assert sender["project_id"] == 3 and peer["registered_at"] == REGISTERED
    args = observations()
    args["sender"] = sender
    args["peer"] = peer
    assert verify_mcp_spawn(**args).name == "QuartzTesla"
    assert profile.read_bytes() == original
    assert len(seen) == 1 and not seen[0].exists()


def test_ephemeral_candidate_lookup_fails_closed_before_helper(profile):
    digest = snapshot_profile(profile)
    with pytest.raises(AdmissionError, match="invalid_candidate"):
        observe_unlisted_peer(profile, profile.parent, name="OtherAgent",
                              agent_id=True, expected_sha256=digest)
    with pytest.raises(AdmissionError, match="profile_changed"):
        observe_unlisted_peer(profile, profile.parent, name="QuartzTesla",
                              agent_id=202, expected_sha256="0" * 64)
    data = json.loads(profile.read_text())
    data["peers"] = [{"name": f"Agent{i}", "id": 300 + i} for i in range(16)]
    profile.write_text(json.dumps(data))
    profile.chmod(0o600)
    with pytest.raises(AdmissionError, match="allowlist_full"):
        observe_unlisted_peer(profile, profile.parent, name="QuartzTesla",
                              agent_id=202, expected_sha256=snapshot_profile(profile))
    assert list(profile.parent.glob(".mcp-candidate-*")) == []


@pytest.mark.parametrize("collision", [
    {"name": "QuartzTesla", "id": 900},
    {"name": "UnrelatedPeer", "id": 202},
])
def test_candidate_pin_refuses_name_or_id_collision(profile, collision):
    data = json.loads(profile.read_text())
    data["peers"] = [collision]
    profile.write_text(json.dumps(data))
    profile.chmod(0o600)
    with pytest.raises(AdmissionError, match="peer_name_or_id_reused"):
        observe_unlisted_peer(profile, profile.parent, name="QuartzTesla",
                              agent_id=202, expected_sha256=snapshot_profile(profile))
    assert list(profile.parent.glob(".mcp-candidate-*")) == []


def test_ephemeral_candidate_pin_cleans_up_on_mail_error(profile, monkeypatch):
    from orrery_chatgpt_mcp import messaging

    class FailingSender:
        def __init__(self, temp_path, helper_root):
            assert temp_path.exists()

        def session(self):
            raise messaging.SenderError("daemon-peer-changed", sent=False)

    monkeypatch.setattr(messaging, "DaemonSender", FailingSender)
    before = profile.read_bytes()
    with pytest.raises(AdmissionError, match="candidate_unverified"):
        observe_unlisted_peer(profile, profile.parent, name="QuartzTesla",
                              agent_id=202, expected_sha256=snapshot_profile(profile))
    assert profile.read_bytes() == before
    assert list(profile.parent.glob(".mcp-candidate-*")) == []


def test_composed_admission_requires_real_private_attestation(profile, monkeypatch):
    from orrery_chatgpt_mcp import auto_allowlist

    calls = []

    def observe(profile_path, helper_root, *, name, agent_id, expected_sha256):
        assert profile_path == profile and helper_root == profile.parent
        assert name == "QuartzTesla" and agent_id == 202
        assert snapshot_profile(profile) == expected_sha256
        calls.append(name)
        obs = observations()
        return obs["sender"], obs["peer"]

    monkeypatch.setattr(auto_allowlist, "observe_unlisted_peer", observe)
    obs = observations()
    before = profile.read_bytes()

    for patch, reason in [
        ({"enabled": False}, "opt_in_required"),
        ({"ledger": {**obs["ledger"], "mcp_authorized": False}}, "no_mcp_spawn_proof"),
        ({"ledger": {**obs["ledger"], "state": "unknown"}}, "no_mcp_spawn_proof"),
        ({"launch": {**obs["launch"], "state": "launching"}}, "not_ready"),
        ({"ledger": {**obs["ledger"], "result": {"mail_agent_id": None}}}, "unattested_spawn"),
    ]:
        kwargs = {"enabled": True, "ledger": obs["ledger"], "launch": obs["launch"],
                  "profile_path": profile, "helper_root": profile.parent}
        kwargs.update(patch)
        with pytest.raises(AdmissionError, match=reason):
            admit_mcp_spawn(**kwargs)
        assert calls == [] and profile.read_bytes() == before

    args = {"enabled": True, "ledger": obs["ledger"], "launch": obs["launch"],
            "profile_path": profile, "helper_root": profile.parent}
    assert admit_mcp_spawn(**args) == "registered"
    assert calls == ["QuartzTesla"]
    assert json.loads(profile.read_text())["peers"][-1] == {"name": "QuartzTesla", "id": 202}
    assert admit_mcp_spawn(**args) == "already_registered"
    assert calls == ["QuartzTesla", "QuartzTesla"]


def test_composed_admission_rejects_cross_project_before_profile_write(profile, monkeypatch):
    from orrery_chatgpt_mcp import auto_allowlist

    def observe(*args, **kwargs):
        obs = observations()
        obs["peer"]["project_id"] = 400
        return obs["sender"], obs["peer"]

    monkeypatch.setattr(auto_allowlist, "observe_unlisted_peer", observe)
    original = profile.read_bytes()
    obs = observations()
    with pytest.raises(AdmissionError, match="peer_binding_changed"):
        admit_mcp_spawn(enabled=True, ledger=obs["ledger"], launch=obs["launch"],
                        profile_path=profile, helper_root=profile.parent)
    assert profile.read_bytes() == original


def _gateway_fixture(profile, tmp_path, **overrides):
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.tools import ToolRegistry
    from orrery_chatgpt_mcp.auth import Principal
    from orrery_chatgpt_mcp.dashboard import DashboardError
    import time

    class Dashboard:
        def __init__(self):
            self.calls = []
            self.state = "launching"
            self.supported = True
            self.drop = False
            self.binding = observations()["ledger"]["result"]

        def name_status(self, name):
            return "available"

        def spawn(self, payload):
            self.calls.append(dict(payload))
            if payload.get("dry_run"):
                result = dict(ok=True, dry_run=True, provider=payload["provider"], model="claude-opus")
                if self.supported and payload.get("mail_create_only"):
                    result["mail_create_only"] = True
                return 200, result
            if self.drop:
                raise DashboardError("unknown", outcome_unknown=True)
            return 200, dict(ok=True, pending=True, model="claude-opus", **self.binding)

        def spawn_status(self, name):
            return 200, dict(name=name, state=self.state, result=self.binding if self.state == "ready" else None)

    connection = Path(json.loads(profile.read_text())["connection"])
    connection.write_text(json.dumps({"kind": "orrery-mail-connection-v1",
                                     "expected_server_instance_id": INSTANCE}))
    connection.chmod(0o600)
    config = GatewayConfig(pairing_secret="A"*43, state_dir=tmp_path/"gw",
                           spawn_enabled=True, spawn_dirs={"test": str(tmp_path)}, spawn_approval="chat",
                           message_enabled=True, message_profile=profile, message_helper_root=tmp_path,
                           auto_register_spawned=overrides.pop("auto_register_spawned", True),
                           **overrides).validate()
    dash = Dashboard()
    operator = Principal("local-operator", frozenset({"orrery.read", "orrery.spawn", "orrery.message"}), None, 0)
    return config, dash, operator, ToolRegistry(config, dash, clock=time.time)


def _launch(registry, operator, key="spawn-test-001"):
    preview = registry.call(operator, "orrery_spawn_preview", {
        "task": "Synthetic test", "directory": "test", "name": "QuartzTesla",
    })
    commit = registry.call(operator, "orrery_spawn_commit", {
        "preview_id": preview["preview_id"], "idempotency_key": key,
    })
    return preview, commit


def test_gateway_opt_in_persists_private_proof_and_worker_reconciles_after_restart(profile, tmp_path, monkeypatch):
    from orrery_chatgpt_mcp.tools import ToolRegistry
    from orrery_chatgpt_mcp import auto_allowlist
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    calls = []

    def admit(**kwargs):
        calls.append(kwargs)
        assert kwargs["expected_authority"] == auto_allowlist.profile_authority(profile).digest
        return "registered"

    monkeypatch.setattr(auto_allowlist, "admit_mcp_spawn", admit)
    preview, commit = _launch(registry, operator)
    assert preview["launch"]["auto_register_recipient"] is True
    assert all(p["mail_create_only"] is True for p in dash.calls)
    assert commit["recipient_registration"]["state"] == "pending"
    assert not any(field.startswith("mail_") for field in commit["launch"])
    registry.reconcile_admissions()
    assert calls == []
    dash.state = "ready"
    registry = ToolRegistry(config, dash)
    assert registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})["recipient_registration"]["state"] == "pending"
    assert calls == []  # status remains read-only
    registry.reconcile_admissions()
    registry.reconcile_admissions()
    assert len(calls) == 1
    assert registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})["recipient_registration"]["state"] == "registered"
    replay = registry.call(operator, "orrery_spawn_commit", {
        "preview_id": preview["preview_id"], "idempotency_key": "spawn-test-001",
    })
    assert replay["recipient_registration"]["state"] == "registered"
    assert len(dash.calls) == 2
    assert not any(field.startswith("mail_") for field in replay["result"])
    dash.binding = dict(dash.binding, mail_agent_id=999)
    assert "recipient_registration" not in registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})


def test_gateway_default_off_does_not_attest_or_admit(profile, tmp_path, monkeypatch):
    from orrery_chatgpt_mcp import auto_allowlist
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path, auto_register_spawned=False)
    monkeypatch.setattr(auto_allowlist, "admit_mcp_spawn", lambda **kw: pytest.fail("unexpected admission"))
    preview, commit = _launch(registry, operator)
    assert "auto_register_recipient" not in preview["launch"]
    assert "recipient_registration" not in commit
    assert all("mail_create_only" not in p for p in dash.calls)
    dash.state = "ready"
    registry.reconcile_admissions()


def test_gateway_refuses_old_dashboard_before_real_spawn(profile, tmp_path):
    from orrery_chatgpt_mcp.tools import ToolError
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    dash.supported = False
    with pytest.raises(ToolError, match="exclusive Mail"):
        _launch(registry, operator)
    assert len(dash.calls) == 1 and dash.calls[0]["dry_run"]


def test_unknown_spawn_never_admitted(profile, tmp_path, monkeypatch):
    from orrery_chatgpt_mcp import auto_allowlist
    from orrery_chatgpt_mcp.tools import ToolError
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    dash.drop = True
    monkeypatch.setattr(auto_allowlist, "admit_mcp_spawn", lambda **kw: pytest.fail("unexpected admission"))
    with pytest.raises(ToolError) as exc:
        _launch(registry, operator)
    assert exc.value.code == "outcome_unknown"
    dash.state = "ready"
    registry.reconcile_admissions()
    assert len(dash.calls) == 2


def test_changed_profile_before_commit_refuses_launch(profile, tmp_path):
    from orrery_chatgpt_mcp.tools import ToolError
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    preview = registry.call(operator, "orrery_spawn_preview", {
        "task": "Synthetic test", "directory": "test", "name": "QuartzTesla",
    })
    data = json.loads(profile.read_text())
    data["name"] = "DifferentSender"
    profile.write_text(json.dumps(data))
    with pytest.raises(ToolError, match="profile changed"):
        registry.call(operator, "orrery_spawn_commit", {
            "preview_id": preview["preview_id"], "idempotency_key": "spawn-test-001",
        })
    assert len(dash.calls) == 1


def test_changed_profile_after_commit_fails_closed(profile, tmp_path):
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    preview, commit = _launch(registry, operator)
    data = json.loads(profile.read_text())
    data["name"] = "DifferentSender"
    profile.write_text(json.dumps(data))
    dash.state = "ready"
    registry.reconcile_admissions()
    assert registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})["recipient_registration"] == {
        "state": "not_registered", "reason": "sender_profile_changed"}
    assert json.loads(profile.read_text())["peers"] == data["peers"]


def test_gateway_requires_message_scope_for_auto_registration(profile, tmp_path):
    from orrery_chatgpt_mcp.auth import Principal
    from orrery_chatgpt_mcp.tools import ToolError
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    read_spawn = Principal(operator.subject, frozenset({"orrery.read", "orrery.spawn"}), None, 0)
    preview = registry.call(operator, "orrery_spawn_preview", {
        "task": "Synthetic test", "directory": "test", "name": "QuartzTesla",
    })
    with pytest.raises(ToolError) as exc:
        registry.call(read_spawn, "orrery_spawn_commit", {
            "preview_id": preview["preview_id"], "idempotency_key": "spawn-test-001",
        })
    assert exc.value.code == "insufficient_scope"
    assert len(dash.calls) == 1
    preview, commit = _launch(registry, read_spawn, "spawn-test-002")
    assert "recipient_registration" not in commit
    assert "mail_create_only" not in dash.calls[-1]


def test_gateway_different_mail_instance_after_preview_is_refused(profile, tmp_path):
    from orrery_chatgpt_mcp.tools import ToolError
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    preview = registry.call(operator, "orrery_spawn_preview", {
        "task": "Synthetic test", "directory": "test", "name": "QuartzTesla",
    })
    connection = Path(json.loads(profile.read_text())["connection"])
    data = json.loads(connection.read_text())
    data["expected_server_instance_id"] = "601f61c2-7d32-45a2-91f2-faa58244c091"
    connection.write_text(json.dumps(data))
    with pytest.raises(ToolError, match="profile changed"):
        registry.call(operator, "orrery_spawn_commit", {
            "preview_id": preview["preview_id"], "idempotency_key": "spawn-test-001",
        })
    assert len(dash.calls) == 1


def test_gateway_receipt_from_wrong_instance_never_admitted(profile, tmp_path, monkeypatch):
    from orrery_chatgpt_mcp import auto_allowlist
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    dash.binding = dict(dash.binding, mail_instance_id="601f61c2-7d32-45a2-91f2-faa58244c091")
    monkeypatch.setattr(auto_allowlist, "admit_mcp_spawn", lambda **kw: pytest.fail("unexpected admission"))
    _, commit = _launch(registry, operator)
    assert commit["outcome"] == "accepted"
    assert commit["recipient_registration"] == {"state": "not_registered", "reason": "unattested_spawn"}
    dash.state = "ready"
    registry.reconcile_admissions()


def test_unsafe_spawn_ledger_cannot_supply_worker_proof(profile, tmp_path, monkeypatch):
    from orrery_chatgpt_mcp.tools import ToolError
    from orrery_chatgpt_mcp import auto_allowlist
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    _launch(registry, operator)
    path = config.state_dir / "spawn-ledger.json"
    path.chmod(0o644)
    dash.state = "ready"
    monkeypatch.setattr(auto_allowlist, "admit_mcp_spawn", lambda **kw: pytest.fail("unexpected admission"))
    with pytest.raises(ToolError, match="unreadable"):
        registry.reconcile_admissions()


def test_worker_full_profile_reports_failure_without_evicting(profile, tmp_path):
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    data = json.loads(profile.read_text())
    data["peers"] = [{"name": f"Fixture{i}", "id": 300 + i} for i in range(16)]
    profile.write_text(json.dumps(data))
    original = profile.read_bytes()
    _, commit = _launch(registry, operator)
    assert commit["outcome"] == "accepted"
    dash.state = "ready"
    registry.reconcile_admissions()
    assert registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})["recipient_registration"] == {
        "state": "not_registered", "reason": "allowlist_full"}
    assert profile.read_bytes() == original


@pytest.mark.parametrize("reason", ["sender_busy", "profile_changed"])
def test_worker_retries_contention_without_spawning_again(profile, tmp_path, monkeypatch, reason):
    from orrery_chatgpt_mcp import auto_allowlist
    config, dash, operator, registry = _gateway_fixture(profile, tmp_path)
    calls = []
    def admit(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise AdmissionError(reason)
        return "registered"
    monkeypatch.setattr(auto_allowlist, "admit_mcp_spawn", admit)
    _launch(registry, operator)
    dash.state = "ready"
    registry.reconcile_admissions()
    assert registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})["recipient_registration"]["state"] == "pending"
    registry.reconcile_admissions()
    assert registry.call(operator, "orrery_spawn_status", {"name": "QuartzTesla"})["recipient_registration"]["state"] == "registered"
    assert len(dash.calls) == 2
