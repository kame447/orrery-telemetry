"""Operator-only launchd message opt-in: immutable helper, default off, no live sends."""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from chatgpt_gateway_support import SRC

from orrery_chatgpt_mcp import pairing, service
from orrery_chatgpt_mcp.config import ConfigError
from orrery_chatgpt_mcp.helper_release import (
    check_profile, install_helper_release, verify_helper_release,
)

ROOT = SRC.parents[2]


def profile_file(tmp_path: Path, peers=None):
    path = tmp_path / "sender.json"
    data = {
        "kind": "orrery-daemon-binding-v2", "name": "ChatRelay", "agent_id": 100,
        "project_key": "/Users/example/project",
        "connection": str(tmp_path / "conn.json"),
        "peers": peers if peers is not None else [{"name": "ExampleAgent", "id": 101}],
        "pause_path": str(tmp_path / "pause"), "lock_path": str(tmp_path / "lock"),
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def helper(tmp_path):
    location, digest = install_helper_release(ROOT, tmp_path / "helpers")
    return location, digest


def _writable(path):
    for node in [path, *path.rglob("*")]:
        if not node.is_symlink():
            os.chmod(node, 0o755 if node.is_dir() else 0o644)


def test_helper_release_exact_content_and_isolated_import(helper):
    root, digest = helper
    assert verify_helper_release(root, digest)
    assert stat.S_IMODE(root.stat().st_mode) == 0o555
    assert set(x.name for x in root.iterdir()) == {"integrations", "packages", "bin", "HELPER.json"}
    done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                           "import sys;sys.path.insert(0,sys.argv[1]);"
                           "from agentstack_codex_app.daemon_runtime import DaemonRuntime;"
                           "print(DaemonRuntime.__name__)",
                           str(root / "integrations/codex_app/src")],
                          capture_output=True, text=True, timeout=12)
    # The second package must also be explicitly available; no ambient site-packages.
    assert done.returncode != 0
    done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                           "import sys;sys.path[:0]=sys.argv[1:];"
                           "from agentstack_codex_app.daemon_runtime import DaemonRuntime;"
                           "print(DaemonRuntime.__name__)",
                           str(root / "integrations/codex_app/src"),
                           str(root / "packages/agentstack_mail/src")],
                          capture_output=True, text=True, timeout=12)
    assert done.returncode == 0 and done.stdout.strip() == "DaemonRuntime"


@pytest.mark.parametrize("tamper", ["modify", "symlink", "extra", "writable", "manifest"])
def test_helper_release_rejects_tampering(tmp_path, tamper):
    root, digest = install_helper_release(ROOT, tmp_path / "helpers")
    _writable(root)
    module = root / "integrations/codex_app/src/agentstack_codex_app/daemon_runtime.py"
    if tamper == "modify":
        module.write_text(module.read_text() + "\n# bad\n")
    elif tamper == "symlink":
        module.unlink()
        module.symlink_to("/etc/hosts")
    elif tamper == "extra":
        (root / "integrations/codex_app/src/agentstack_codex_app/unexpected.py").write_text("pass\n")
    elif tamper == "manifest":
        (root / "HELPER.json").write_text("{}")
    else:
        module.chmod(0o666)
    for node in [root, *root.rglob("*")]:
        if not node.is_symlink():
            os.chmod(node, 0o555 if node.is_dir() else 0o444)
    if tamper == "writable":
        module.chmod(0o666)
    assert not verify_helper_release(root, digest)
    with pytest.raises(ConfigError, match="unsafe"):
        install_helper_release(ROOT, tmp_path / "helpers")


def test_canonical_private_profile_required_before_enabling(tmp_path):
    profile = profile_file(tmp_path)
    check_profile(profile)
    profile.chmod(0o644)
    with pytest.raises(ConfigError, match="owner-only"):
        check_profile(profile)
    profile.chmod(0o600)
    profile_file(tmp_path, peers=[{"name": "ChatRelay", "id": 101}])
    with pytest.raises(ConfigError, match="duplicate"):
        check_profile(profile)
    profile_file(tmp_path, peers=[{"name": "ExampleAgent", "id": 100}])
    with pytest.raises(ConfigError, match="duplicate"):
        check_profile(profile)
    profile_file(tmp_path, peers=[])
    with pytest.raises(ConfigError, match="allowlist"):
        check_profile(profile)


def test_service_default_off_and_explicit_optin_plist(tmp_path, helper):
    root, digest = helper
    plan = service.ServicePlan(python=sys.executable, release_dir=tmp_path / "release",
                               digest="f" * 64, state_dir=tmp_path)
    default = plistlib.loads(service.render_plist(plan))
    env = default["EnvironmentVariables"]
    assert env["ORRERY_MCP_ENABLE_MESSAGE"] == "0"
    assert "ORRERY_MCP_MESSAGE_PROFILE" not in env
    assert "ORRERY_MCP_MESSAGE_HELPER_ROOT" not in env
    assert env["ORRERY_MCP_ENABLE_SPAWN"] == "0"
    profile = profile_file(tmp_path)
    plan = service.ServicePlan(python=sys.executable, release_dir=tmp_path / "release", digest="f" * 64,
                               state_dir=tmp_path, message_profile=profile, message_helper_root=root,
                               message_helper_digest=digest, message_max_per_hour=3)
    env = plistlib.loads(service.render_plist(plan))["EnvironmentVariables"]
    assert env["ORRERY_MCP_ENABLE_MESSAGE"] == "1"
    assert env["ORRERY_MCP_MESSAGE_PROFILE"] == str(profile)
    assert env["ORRERY_MCP_MESSAGE_HELPER_ROOT"] == str(root)
    assert env["ORRERY_MCP_MESSAGE_HELPER_DIGEST"] == digest
    assert env["ORRERY_MCP_MESSAGE_MAX_PER_HOUR"] == "3"
    assert env["ORRERY_MCP_ENABLE_SPAWN"] == "0"
    assert "AGENTSTACK_MAIL" not in str(env)
    assert all(not k.startswith("PYTHON") for k in env)


def test_bootstrap_does_not_import_tampered_helper(tmp_path, helper):
    root, digest = helper
    release, release_digest = service.install_release(SRC, tmp_path / "releases")
    module = root / "packages/agentstack_mail/src/agentstack_mail/enrollment_protocol.py"
    _writable(root)
    module.write_text(module.read_text() + "\n# substituted\n")
    for node in [root, *root.rglob("*")]:
        if not node.is_symlink():
            os.chmod(node, 0o555 if node.is_dir() else 0o444)
    env = {"ORRERY_MCP_ENABLE_MESSAGE": "1", "ORRERY_MCP_MESSAGE_HELPER_ROOT": str(root),
           "ORRERY_MCP_MESSAGE_HELPER_DIGEST": digest, "HOME": str(tmp_path)}
    done = subprocess.run(service.program_arguments(service.ServicePlan(
        python=sys.executable, release_dir=release, digest=release_digest, state_dir=tmp_path)),
        env=env, capture_output=True, text=True, timeout=12)
    assert done.returncode == 78
    assert "message helper release integrity check failed" in done.stderr
    assert "orrery-chatgpt-mcp listening" not in done.stderr


def test_optin_refuses_bad_profile_before_launchctl_changes(tmp_path, helper):
    from test_chatgpt_gateway_service import FakeLaunchctl
    root, digest = helper
    state = tmp_path / "state"
    pairing.create_secret(state)
    release, release_digest = service.install_release(SRC, state / "releases")
    profile = profile_file(tmp_path)
    profile.chmod(0o644)
    fake = FakeLaunchctl()
    plan = service.ServicePlan(
        python=sys.executable, release_dir=release, digest=release_digest, state_dir=state,
        message_profile=profile, message_helper_root=root, message_helper_digest=digest,
    )
    with pytest.raises(ConfigError, match="owner-only"):
        service.install_service(plan, runner=fake, launch_agents=tmp_path / "LA", verify=False)
    assert not any(action in ("bootout", "bootstrap") for action in fake.verbs())
