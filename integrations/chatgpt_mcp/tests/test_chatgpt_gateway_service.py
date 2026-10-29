"""launchd service: pinned read-only release, secret-free plist, no spawn, loopback only."""
from __future__ import annotations

import os
import plistlib
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from chatgpt_gateway_support import SRC, free_port


class FakeLaunchctl:
    """Records launchctl calls; ``loaded`` mimics `launchctl print` for the label."""

    def __init__(self, *, loaded=False, bootstrap_rc=0):
        self.calls: list[list[str]] = []
        self.loaded = loaded
        self.bootstrap_rc = bootstrap_rc

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if args[0] != "launchctl":
            return subprocess.run(args, **kwargs)
        verb = args[1]
        rc = 0
        if verb == "print":
            rc = 0 if self.loaded else 113
        elif verb == "bootout":
            self.loaded = False
        elif verb == "bootstrap":
            rc = self.bootstrap_rc
            self.loaded = rc == 0
        return subprocess.CompletedProcess(args, rc, "", "boom" if rc else "")

    def verbs(self):
        return [c[1] for c in self.calls if c[0] == "launchctl"]


@pytest.fixture
def paired(tmp_path):
    from orrery_chatgpt_mcp import pairing

    state = tmp_path / "state"
    secret = pairing.create_secret(state)
    return state, secret


@pytest.fixture
def release(tmp_path):
    from orrery_chatgpt_mcp import service

    return service.install_release(SRC, tmp_path / "releases", source_commit="f" * 40)


def plan_for(release, state, **kw):
    from orrery_chatgpt_mcp import service

    return service.ServicePlan(python=sys.executable, release_dir=release, state_dir=state,
                               port=kw.pop("port", free_port()), **kw)


# -- release ------------------------------------------------------------------------

def test_release_is_read_only_pinned_copy(tmp_path, release):
    from orrery_chatgpt_mcp import service

    info = service.read_release(release)
    assert info["intact"] and info["source_commit"] == "f" * 40
    assert info["digest"] == service.release_digest(SRC / "orrery_chatgpt_mcp")
    for path in [release, *release.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert not mode & 0o222, f"{path} is writable ({oct(mode)})"
    assert not list(release.rglob("__pycache__"))
    assert stat.S_IMODE((tmp_path / "releases").stat().st_mode) == 0o700


def test_same_package_reuses_release_and_modified_release_is_refused(tmp_path, release):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    assert service.install_release(SRC, tmp_path / "releases") == release
    victim = release / "orrery_chatgpt_mcp" / "tools.py"
    os.chmod(victim, 0o644)
    victim.write_text(victim.read_text() + "\n# tampered\n")
    assert service.read_release(release)["intact"] is False
    with pytest.raises(ConfigError, match="modified"):
        service.install_release(SRC, tmp_path / "releases")


def test_release_refuses_symlinks_and_non_package(tmp_path):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    src = tmp_path / "src"
    pkg = src / "orrery_chatgpt_mcp"
    pkg.mkdir(parents=True)
    with pytest.raises(ConfigError, match="not the"):
        service.install_release(src, tmp_path / "rel")
    (pkg / "__main__.py").write_text("")
    (pkg / "evil.py").symlink_to("/etc/hosts")
    with pytest.raises(ConfigError, match="symlink"):
        service.install_release(src, tmp_path / "rel")


# -- plist ----------------------------------------------------------------------------

def test_plist_has_no_secret_no_spawn_and_loopback_only(release, paired):
    from orrery_chatgpt_mcp import service

    state, secret = paired
    raw = service.render_plist(plan_for(release, state, port=8790))
    assert secret.encode() not in raw
    spec = plistlib.loads(raw)
    env = spec["EnvironmentVariables"]
    assert spec["Label"] == "org.agentstack.chatgpt-mcp"
    assert spec["ProgramArguments"] == [sys.executable, "-s", "-m", "orrery_chatgpt_mcp", "serve"]
    assert env["ORRERY_MCP_ENABLE_SPAWN"] == "0" and "ORRERY_MCP_SPAWN_DIRS" not in env
    assert env["ORRERY_MCP_BIND_HOST"] == "127.0.0.1" and env["ORRERY_MCP_AUTH"] == "pairing"
    assert env["PYTHONPATH"] == str(release) and env["PYTHONSAFEPATH"] == "1"
    assert spec["Umask"] == 0o077 and spec["RunAtLoad"] and spec["KeepAlive"]
    assert spec["StandardErrorPath"] == str(state / "gateway.log")
    assert not any(k.startswith(("CONTROL_PLANE", "OPENAI")) for k in env)


def test_plist_environment_actually_serves_the_pinned_release(tmp_path, release, paired, dashboard):
    """Run the plist's exact program and environment (as launchd would) and probe it."""
    from orrery_chatgpt_mcp import service

    state, secret = paired
    plan = plan_for(release, state, dashboard_url=dashboard.url)
    spec = plistlib.loads(service.render_plist(plan))
    env = dict(spec["EnvironmentVariables"], HOME=str(tmp_path))
    log = open(plan.log_path, "ab")
    proc = subprocess.Popen(spec["ProgramArguments"], cwd=spec["WorkingDirectory"], env=env, stdout=log, stderr=log)
    try:
        status = service.wait_until_answering(plan.port, secret, deadline=20)
        assert status["dashboard"]["reachable"] is True
        assert status["capabilities"]["spawn_enabled_on_server"] is False
        assert status["connection"]["auth_mode"] == "pairing"
        tools = _list_tools(plan.port, secret)
        assert "orrery_spawn_commit" not in tools and "orrery_status" in tools
        with pytest.raises(ConnectionError, match="404"):
            service.probe_status(plan.port, "x" * 43)
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()
    text = plan.log_path.read_text()
    assert secret not in text and "listening on http://127.0.0.1:" in text and "spawn disabled" in text


def _list_tools(port, secret):
    import http.client
    import json

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", f"/mcp/{secret}", body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                 headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
    data = json.loads(conn.getresponse().read())
    conn.close()
    return {t["name"] for t in data["result"]["tools"]}


# -- install / uninstall ---------------------------------------------------------------

def test_install_writes_plist_and_bootstraps(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, secret = paired
    fake = FakeLaunchctl()
    agents = tmp_path / "LaunchAgents"
    plan = plan_for(release, state)
    assert service.install_service(plan, runner=fake, launch_agents=agents, verify=False) == {}
    target = agents / "org.agentstack.chatgpt-mcp.plist"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert secret.encode() not in target.read_bytes()
    assert fake.verbs() == ["print", "bootstrap"]
    assert fake.calls[-1] == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)]
    assert stat.S_IMODE(plan.log_path.stat().st_mode) == 0o600


def test_install_tightens_an_existing_world_readable_log(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, _ = paired
    plan = plan_for(release, state)
    plan.log_path.write_text("old line\n")
    os.chmod(plan.log_path, 0o644)
    service.install_service(plan, runner=FakeLaunchctl(), launch_agents=tmp_path / "LA", verify=False)
    assert stat.S_IMODE(plan.log_path.stat().st_mode) == 0o600
    assert plan.log_path.read_text() == "old line\n"


def test_reinstall_boots_out_the_old_service_first(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, _ = paired
    fake = FakeLaunchctl(loaded=True)
    service.install_service(plan_for(release, state), runner=fake, launch_agents=tmp_path / "LA", verify=False)
    assert fake.verbs() == ["print", "bootout", "bootstrap"]


@pytest.mark.parametrize("problem", ["unpaired", "dashboard", "python", "port", "release", "bootstrap"])
def test_install_refuses_bad_setups(tmp_path, release, paired, problem):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, _ = paired
    fake = FakeLaunchctl(bootstrap_rc=5 if problem == "bootstrap" else 0)
    kw = {}
    holder = None
    if problem == "unpaired":
        state = tmp_path / "other-state"
    if problem == "dashboard":
        kw["dashboard_url"] = "http://192.168.1.5:8770"
    if problem == "python":
        kw["python"] = "python3"
    if problem == "port":
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        kw["port"] = holder.getsockname()[1]
    if problem == "release":
        release = tmp_path / "missing-release"
    plan = plan_for(release, state, **{k: v for k, v in kw.items() if k != "python"})
    if "python" in kw:
        plan = service.ServicePlan(python=kw["python"], release_dir=release, state_dir=state, port=plan.port)
    agents = tmp_path / "LA"
    try:
        with pytest.raises(ConfigError):
            service.install_service(plan, runner=fake, launch_agents=agents, verify=False)
    finally:
        if holder:
            holder.close()
    if problem != "bootstrap":
        assert "bootstrap" not in fake.verbs()
        assert not (agents / "org.agentstack.chatgpt-mcp.plist").exists()


def test_uninstall_keeps_pairing_and_refuses_foreign_plist(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, secret = paired
    agents = tmp_path / "LA"
    fake = FakeLaunchctl()
    service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    assert service.uninstall_service(runner=fake, launch_agents=agents) is True
    assert fake.verbs()[-1] == "bootout"
    assert not (agents / "org.agentstack.chatgpt-mcp.plist").exists()
    assert (state / "pairing.json").exists()
    assert service.uninstall_service(runner=fake, launch_agents=agents) is False
    (agents / "org.agentstack.chatgpt-mcp.plist").write_bytes(plistlib.dumps({"Label": "com.example.other"}))
    with pytest.raises(ConfigError):
        service.uninstall_service(runner=fake, launch_agents=agents)
    assert (agents / "org.agentstack.chatgpt-mcp.plist").exists()


def test_status_reports_release_and_never_the_secret(tmp_path, release, paired, capsys):
    from orrery_chatgpt_mcp import service

    state, secret = paired
    agents = tmp_path / "LA"
    fake = FakeLaunchctl()
    service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    out = service.service_status(runner=fake, launch_agents=agents)
    assert out["loaded"] and out["plist"] and out["spawn"] is False
    assert out["release_intact"] and out["source_commit"] == "f" * 40
    assert out["answering"] is False  # nothing is listening in this test
    assert secret not in repr(out)


# -- CLI ----------------------------------------------------------------------------------

def _cli(args, env_extra, home):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PYTHONPATH": str(SRC), **env_extra}
    return subprocess.run([sys.executable, "-m", "orrery_chatgpt_mcp", *args], env=env,
                          capture_output=True, text=True, timeout=60)


def test_pair_quiet_never_prints_the_secret(tmp_path):
    from orrery_chatgpt_mcp import pairing

    state = tmp_path / "state"
    done = _cli(["pair", "--quiet"], {"ORRERY_MCP_STATE_DIR": str(state)}, tmp_path)
    assert done.returncode == 0, done.stderr
    secret = pairing.read_secret(state)
    assert secret not in done.stdout + done.stderr and "url" in done.stdout
    again = _cli(["pair", "--quiet"], {"ORRERY_MCP_STATE_DIR": str(state)}, tmp_path)
    assert again.returncode == 2 and pairing.read_secret(state) == secret
    assert _cli(["pair", "--quiet", "--quiet"], {"ORRERY_MCP_STATE_DIR": str(state)}, tmp_path).returncode == 64


def test_service_cli_refuses_unpaired_install_without_touching_launchd(tmp_path):
    done = _cli(["service", "install", "--python", sys.executable],
                {"ORRERY_MCP_STATE_DIR": str(tmp_path / "state"), "ORRERY_MCP_PORT": str(free_port())}, tmp_path)
    assert done.returncode == 2 and "pair" in done.stderr
    assert not (tmp_path / "Library" / "LaunchAgents").exists()
    assert _cli(["service", "install", "--bogus", "x"], {"ORRERY_MCP_STATE_DIR": str(tmp_path / "s")}, tmp_path).returncode == 2
    assert _cli(["service", "frobnicate"], {"ORRERY_MCP_STATE_DIR": str(tmp_path / "s")}, tmp_path).returncode == 2
