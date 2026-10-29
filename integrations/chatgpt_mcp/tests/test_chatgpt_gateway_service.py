"""launchd service: pinned read-only release, secret-free plist, no spawn, loopback only.

Review findings L01–L06 (REVIEW-astra-launchd) each have a regression test here.
"""
from __future__ import annotations

import json
import os
import plistlib
import socket
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from chatgpt_gateway_support import SRC, free_port

needs_lsof = pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/sbin/lsof"),
                                reason="launchd service and its lsof peer check are macOS only")


class FakeLaunchctl:
    """Records launchctl calls and passes anything else (python, lsof) through.

    ``pid`` is what `launchctl print` reports for the running job and ``path``
    the plist it was loaded from (set by bootstrap). ``print_errors`` makes
    the next `print` calls fail the way an inspection error would.
    """

    def __init__(self, *, loaded=False, pid=None, path=None, bootstrap_rc=0, bootout_rc=0, print_errors=()):
        self.calls: list[list[str]] = []
        self.loaded = loaded
        self.pid = pid
        self.path = path
        self.bootstrap_rc = bootstrap_rc
        self.bootout_rc = bootout_rc
        self.print_errors = list(print_errors)

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if args[0] != "launchctl":
            return subprocess.run(args, **kwargs)
        verb = args[1]
        rc, out, err = 0, "", ""
        if verb == "print":
            if self.print_errors and self.print_errors.pop(0):
                return subprocess.CompletedProcess(args, 5, "", "Input/output error")
            if not self.loaded:
                return subprocess.CompletedProcess(args, 113, "", f'Bad request.\nCould not find service "{args[2]}"')
            out = f"{args[2]} = {{\n\tpath = {self.path}\n\tstate = running\n"
            out += f"\tpid = {self.pid}\n}}\n" if self.pid else "}\n"
        elif verb == "bootout":
            rc = self.bootout_rc
            if rc == 0:
                self.loaded = False
        elif verb == "bootstrap":
            rc = self.bootstrap_rc
            self.loaded = rc == 0
            if rc == 0:
                self.path = args[3]
            else:
                err = "boom"
        return subprocess.CompletedProcess(args, rc, out, err)

    def verbs(self):
        return [c[1] for c in self.calls if c[0] == "launchctl"]


TARGET = "org.agentstack.chatgpt-mcp.plist"


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

    path, digest = release
    return service.ServicePlan(python=kw.pop("python", sys.executable), release_dir=path, digest=digest,
                               state_dir=state, port=kw.pop("port", free_port()), **kw)


def writable(path):
    for p in [path, *path.rglob("*")]:
        if not p.is_symlink():
            os.chmod(p, 0o755 if p.is_dir() else 0o644)


def start_like_launchd(plan, home, extra_env=None, python=None):
    """Start the plist's exact program and environment, plus hostile inherited variables."""
    from orrery_chatgpt_mcp import service

    spec = plistlib.loads(service.render_plist(plan))
    args = list(spec["ProgramArguments"])
    if python:
        args[0] = python
    env = {**(extra_env or {}), **spec["EnvironmentVariables"], "HOME": str(home)}
    log = open(plan.log_path, "ab")
    proc = subprocess.Popen(args, cwd=spec["WorkingDirectory"], env=env, stdout=log, stderr=log)
    log.close()
    return proc


def stop(proc):
    proc.terminate()
    proc.wait(10)


# -- release ------------------------------------------------------------------------

def test_release_is_read_only_pinned_copy(tmp_path, release):
    from orrery_chatgpt_mcp import service

    path, digest = release
    assert service.verify_release(path, digest)
    info = service.read_release(path)
    assert info["digest"] == digest == service.release_digest(SRC / "orrery_chatgpt_mcp")
    assert info["source_commit"] == "f" * 40
    for p in [path, *path.rglob("*")]:
        assert not stat.S_IMODE(p.stat().st_mode) & 0o222, p
    assert sorted(os.listdir(path)) == ["RELEASE.json", "orrery_chatgpt_mcp"]
    assert stat.S_IMODE((tmp_path / "releases").stat().st_mode) == 0o700


def test_same_package_reuses_release_and_modified_source_is_refused(tmp_path, release):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    path, digest = release
    assert service.install_release(SRC, tmp_path / "releases") == release
    writable(path)
    victim = path / "orrery_chatgpt_mcp" / "tools.py"
    victim.write_text(victim.read_text() + "\n# tampered\n")
    assert not service.verify_release(path, digest)
    with pytest.raises(ConfigError, match="modified or unsafe"):
        service.install_release(SRC, tmp_path / "releases")


@pytest.mark.parametrize("extra", ["pycache", "sitecustomize", "root_symlink", "writable_file", "non_py"])
def test_l02_unverified_bytes_make_a_release_unusable(tmp_path, release, extra):
    """L02: caches, root extras, symlinked or writable releases fail the full check."""
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    path, digest = release
    writable(path)
    if extra == "pycache":
        (path / "orrery_chatgpt_mcp" / "__pycache__").mkdir()
        (path / "orrery_chatgpt_mcp" / "__pycache__" / "server.cpython-313.pyc").write_bytes(b"x")
    if extra == "sitecustomize":
        (path / "sitecustomize.py").write_text("print('UNHASHED')\n")
    if extra == "non_py":
        (path / "orrery_chatgpt_mcp" / "evil.so").write_bytes(b"x")
    if extra == "root_symlink":
        real = tmp_path / "elsewhere"
        os.rename(path, real)
        path.symlink_to(real)
    if extra != "writable_file":
        for p in [path, *path.rglob("*")]:
            if not p.is_symlink():
                os.chmod(p, 0o555 if p.is_dir() else 0o444)
    assert not service.verify_release(path, digest)
    with pytest.raises(ConfigError, match="modified or unsafe"):
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
    with pytest.raises(ConfigError, match="unexpected"):
        service.install_release(src, tmp_path / "rel")


def test_source_changed_during_copy_is_never_published(tmp_path, monkeypatch):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    src = tmp_path / "src"
    import shutil

    shutil.copytree(SRC / "orrery_chatgpt_mcp", src / "orrery_chatgpt_mcp", ignore=shutil.ignore_patterns("__pycache__"))
    real_copy = shutil.copyfile

    def racing_copy(a, b, **kw):
        real_copy(a, b, **kw)
        if Path(b).name == "tools.py":
            Path(b).write_text("# swapped while copying\n")

    monkeypatch.setattr(service.shutil, "copyfile", racing_copy)
    with pytest.raises(ConfigError, match="changed while it was copied"):
        service.install_release(src, tmp_path / "rel")
    assert not [p for p in (tmp_path / "rel").iterdir() if not p.name.startswith(".")]


# -- plist and startup ----------------------------------------------------------------------

def test_plist_has_no_secret_no_spawn_and_isolated_startup(release, paired):
    from orrery_chatgpt_mcp import service

    state, secret = paired
    plan = plan_for(release, state, port=8790)
    raw = service.render_plist(plan)
    assert secret.encode() not in raw
    spec = plistlib.loads(raw)
    env = spec["EnvironmentVariables"]
    assert spec["Label"] == "org.agentstack.chatgpt-mcp"
    assert spec["ProgramArguments"] == [sys.executable, "-I", "-S", "-B", "-c", service.BOOTSTRAP, str(plan.release_dir), plan.digest]
    assert env["ORRERY_MCP_ENABLE_SPAWN"] == "0" and "ORRERY_MCP_SPAWN_DIRS" not in env
    assert env["ORRERY_MCP_BIND_HOST"] == "127.0.0.1" and env["ORRERY_MCP_AUTH"] == "pairing"
    assert not any(k.startswith("PYTHON") for k in env)
    assert spec["Umask"] == 0o077 and spec["RunAtLoad"] and spec["KeepAlive"]
    assert spec["StandardErrorPath"] == str(state / "gateway.log")


@needs_lsof
def test_l01_plist_startup_ignores_hostile_python_environment(tmp_path, release, paired, dashboard):
    """L01: PYTHONPATH / PYTHONHOME / PYTHONPYCACHEPREFIX / PYTHONSTARTUP cannot replace the pinned code."""
    from orrery_chatgpt_mcp import service

    state, secret = paired
    fake = tmp_path / "fake" / "orrery_chatgpt_mcp"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("raise SystemExit('REPLACEMENT_EXECUTED')\n")
    (tmp_path / "fake" / "sitecustomize.py").write_text("raise SystemExit('SITECUSTOMIZE_EXECUTED')\n")
    hostile = {"PYTHONPATH": str(tmp_path / "fake"), "PYTHONHOME": str(tmp_path / "nohome"),
               "PYTHONPYCACHEPREFIX": str(tmp_path / "cache"), "PYTHONSTARTUP": str(tmp_path / "fake" / "sitecustomize.py"),
               "PYTHONINSPECT": "1"}
    plan = plan_for(release, state, dashboard_url=dashboard.url)
    proc = start_like_launchd(plan, tmp_path, hostile)
    try:
        status = service.wait_until_answering(plan, secret, FakeLaunchctl(loaded=True, pid=proc.pid), deadline=20)
        assert status["dashboard"]["reachable"] is True
        assert status["capabilities"]["spawn_enabled_on_server"] is False
        assert "orrery_spawn_commit" not in _list_tools(plan.port, secret)
    finally:
        stop(proc)
    text = plan.log_path.read_text()
    assert "EXECUTED" not in text and secret not in text and "spawn disabled" in text
    assert not (tmp_path / "cache").exists()


@needs_lsof
def test_l01_interpreter_pth_and_sitecustomize_are_not_loaded(tmp_path, release, paired, dashboard):
    """L01: .pth files and sitecustomize in the interpreter's own site-packages do not run (-S)."""
    from orrery_chatgpt_mcp import service

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    site = Path(subprocess.run([str(venv / "bin" / "python"), "-c", "import site; print(site.getsitepackages()[0])"],
                               capture_output=True, text=True, check=True).stdout.strip())
    marker = tmp_path / "marker"
    (site / "evil.pth").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).write_text('PTH')\n")
    (site / "sitecustomize.py").write_text(f"import pathlib; pathlib.Path({str(marker)!r}).write_text('SITE')\n")
    state, secret = paired
    plan = plan_for(release, state, dashboard_url=dashboard.url, python=str(venv / "bin" / "python"))
    proc = start_like_launchd(plan, tmp_path)
    try:
        service.wait_until_answering(plan, secret, FakeLaunchctl(loaded=True, pid=proc.pid), deadline=20)
    finally:
        stop(proc)
    assert not marker.exists()


@pytest.mark.parametrize("extra", ["pycache", "sitecustomize"])
def test_l02_tampered_release_never_starts(tmp_path, release, paired, extra):
    """L02 at runtime: BOOTSTRAP refuses before importing anything from the release."""
    path, _ = release
    writable(path)
    if extra == "pycache":
        (path / "orrery_chatgpt_mcp" / "__pycache__").mkdir()
    else:
        (path / "sitecustomize.py").write_text("print('UNHASHED_SITECUSTOMIZE')\n")
    for p in [path, *path.rglob("*")]:
        os.chmod(p, 0o555 if p.is_dir() else 0o444)
    state, _ = paired
    plan = plan_for(release, state)
    proc = start_like_launchd(plan, tmp_path)
    assert proc.wait(20) == 78
    text = plan.log_path.read_text()
    assert "release check failed" in text and "UNHASHED" not in text


def _list_tools(port, secret):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", f"/mcp/{secret}", body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                 headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
    data = json.loads(conn.getresponse().read())
    conn.close()
    return {t["name"] for t in data["result"]["tools"]}


# -- who answers (L03, L05) ------------------------------------------------------------------

class Impostor:
    """A loopback listener in this test process that records request paths."""

    def __init__(self, mode):
        self.paths: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.paths.append(self.path)
                if mode == "reflect":
                    self.wfile.write(f"HTTP/1.1 {self.requestline}\r\n\r\n".encode())
                    return
                body = json.dumps({"result": {"structuredContent": {
                    "gateway": {"name": "orrery-chatgpt-mcp"}, "connection": {"auth_mode": "pairing"},
                    "capabilities": {"spawn_enabled_on_server": True}, "dashboard": {"reachable": True}}}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@needs_lsof
def test_l03_secret_is_not_sent_to_a_listener_that_is_not_the_job(tmp_path, paired):
    from orrery_chatgpt_mcp import service

    _, secret = paired
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    imp = Impostor("forged")
    try:
        with pytest.raises(service.VerifyError, match="not the service process") as err:
            service.probe_status(imp.port, secret, other.pid)
        assert imp.paths == [] and secret not in str(err.value)
    finally:
        imp.close()
        stop(other)


@needs_lsof
def test_l03_forged_configuration_is_not_accepted(paired):
    from orrery_chatgpt_mcp import service

    _, secret = paired
    imp = Impostor("forged")  # owned by this process, so the peer check passes
    try:
        with pytest.raises(service.VerifyError, match="service configuration"):
            service.probe_status(imp.port, secret, os.getpid())
    finally:
        imp.close()


@needs_lsof
def test_l05_reflected_status_line_never_carries_the_secret(paired):
    from orrery_chatgpt_mcp import service

    _, secret = paired
    imp = Impostor("reflect")
    try:
        with pytest.raises(service.VerifyError) as err:
            service.probe_status(imp.port, secret, os.getpid())
        import traceback

        assert secret not in "".join(traceback.format_exception(err.value))
        assert err.value.__cause__ is None and err.value.__suppress_context__
    finally:
        imp.close()


def test_l03_status_does_not_probe_when_the_job_is_not_running(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, secret = paired
    agents = tmp_path / "LA"
    fake = FakeLaunchctl()
    service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    fake.pid = None
    out = service.service_status(runner=fake, launch_agents=agents)
    assert out["loaded"] and not out["running"] and out["answering"] is False
    assert out["release_intact"] is True and out["source_commit"] == "f" * 40 and out["spawn"] is False
    assert not any(c[0].endswith("lsof") for c in fake.calls)
    assert secret not in repr(out)


# -- install / uninstall (L04, L06) ------------------------------------------------------------

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


def test_reinstall_boots_out_its_own_service_first(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, _ = paired
    agents = tmp_path / "LA"
    service.install_service(plan_for(release, state), runner=FakeLaunchctl(), launch_agents=agents, verify=False)
    fake = FakeLaunchctl(loaded=True, path=str(agents / TARGET))
    service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    assert fake.verbs() == ["print", "bootout", "print", "bootstrap"]


def test_reinstall_accepts_the_first_version_plist(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, _ = paired
    agents = tmp_path / "LA"
    agents.mkdir()
    (agents / "org.agentstack.chatgpt-mcp.plist").write_bytes(plistlib.dumps({
        "Label": "org.agentstack.chatgpt-mcp", "ProgramArguments": ["/usr/bin/python3", "-s", "-m", "orrery_chatgpt_mcp", "serve"]}))
    fake = FakeLaunchctl(loaded=True, path=str(agents / TARGET))
    service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    assert "bootstrap" in fake.verbs()


def test_l04_failed_bootout_is_reported_and_keeps_the_plist(tmp_path, release, paired, monkeypatch):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    monkeypatch.setattr(service, "BOOTOUT_POLLS", 3)

    state, _ = paired
    agents = tmp_path / "LA"
    service.install_service(plan_for(release, state), runner=FakeLaunchctl(), launch_agents=agents, verify=False)
    stuck = FakeLaunchctl(loaded=True, path=str(agents / TARGET), bootout_rc=5)
    with pytest.raises(ConfigError, match="still loaded"):
        service.uninstall_service(runner=stuck, launch_agents=agents)
    assert (agents / "org.agentstack.chatgpt-mcp.plist").exists()
    with pytest.raises(ConfigError, match="still loaded"):
        service.install_service(plan_for(release, state), runner=stuck, launch_agents=agents, verify=False)
    assert "bootstrap" not in stuck.verbs()


def test_l04_pre_bootstrap_write_failure_is_a_config_error(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, _ = paired
    plan = plan_for(release, state)
    plan.log_path.symlink_to(tmp_path / "elsewhere.log")
    fake = FakeLaunchctl()
    with pytest.raises(ConfigError, match="could not write"):
        service.install_service(plan, runner=fake, launch_agents=tmp_path / "LA", verify=False)
    assert "bootstrap" not in fake.verbs()


@pytest.mark.parametrize("kind", ["other_label", "same_label_other_program", "garbage"])
def test_l06_foreign_plist_is_refused_before_anything_is_stopped(tmp_path, release, paired, kind):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, _ = paired
    agents = tmp_path / "LA"
    agents.mkdir()
    target = agents / "org.agentstack.chatgpt-mcp.plist"
    if kind == "garbage":
        target.write_bytes(b"not a plist")
    else:
        label = "com.example.other" if kind == "other_label" else "org.agentstack.chatgpt-mcp"
        target.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/false"]}))
    before = target.read_bytes()
    for action in ("install", "uninstall"):
        fake = FakeLaunchctl(loaded=True, path=str(target))
        with pytest.raises(ConfigError, match="not a plist this command wrote"):
            if action == "install":
                service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
            else:
                service.uninstall_service(runner=fake, launch_agents=agents)
        assert "bootout" not in fake.verbs() and "bootstrap" not in fake.verbs()
        assert target.read_bytes() == before


def test_install_refuses_a_job_loaded_but_without_its_plist(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, _ = paired
    fake = FakeLaunchctl(loaded=True, path=str(tmp_path / "LA" / TARGET))
    with pytest.raises(ConfigError, match="is gone"):
        service.install_service(plan_for(release, state), runner=fake, launch_agents=tmp_path / "LA", verify=False)
    assert fake.verbs() == ["print"]


@pytest.mark.parametrize("own_plist_on_disk", [True, False])
def test_rr02_job_loaded_from_another_plist_is_never_stopped(tmp_path, release, paired, own_plist_on_disk):
    """RR02: the default plist on disk is not proof of what launchd loaded."""
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, _ = paired
    agents = tmp_path / "LA"
    if own_plist_on_disk:
        service.install_service(plan_for(release, state), runner=FakeLaunchctl(), launch_agents=agents, verify=False)
    for action in ("install", "uninstall"):
        fake = FakeLaunchctl(loaded=True, path="/private/tmp/unrelated-job.plist", pid=1)
        with pytest.raises(ConfigError, match="unrelated-job.plist"):
            if action == "install":
                service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
            else:
                service.uninstall_service(runner=fake, launch_agents=agents)
        assert fake.verbs() == ["print"]
        assert (agents / TARGET).exists() == own_plist_on_disk
    out = service.service_status(runner=FakeLaunchctl(loaded=True, path="/private/tmp/unrelated-job.plist", pid=1),
                                 launch_agents=agents)
    assert "unrelated-job.plist" in out["error"] and "answering" not in out


@pytest.mark.parametrize("failing", ["first", "confirmation"])
@pytest.mark.parametrize("bootout_rc", [0, 5])
def test_rr01_launchctl_inspection_error_is_never_taken_as_absence(tmp_path, release, paired, failing, bootout_rc, monkeypatch):
    """RR01: only launchd's explicit "no such service" counts as not loaded."""
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    monkeypatch.setattr(service, "BOOTOUT_POLLS", 3)
    state, _ = paired
    agents = tmp_path / "LA"
    service.install_service(plan_for(release, state), runner=FakeLaunchctl(), launch_agents=agents, verify=False)
    errors = [True] if failing == "first" else [False, True, True, True]
    fake = FakeLaunchctl(loaded=True, path=str(agents / TARGET), bootout_rc=bootout_rc, print_errors=errors)
    with pytest.raises(ConfigError, match="cannot tell whether"):
        service.uninstall_service(runner=fake, launch_agents=agents)
    assert (agents / TARGET).exists()
    if failing == "first":
        assert fake.verbs() == ["print"]
    fake = FakeLaunchctl(loaded=True, path=str(agents / TARGET), bootout_rc=bootout_rc, print_errors=list(errors))
    with pytest.raises(ConfigError, match="cannot tell whether"):
        service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    assert "bootstrap" not in fake.verbs()
    out = service.service_status(runner=FakeLaunchctl(print_errors=[True]), launch_agents=agents)
    assert "cannot tell whether" in out["error"] and "loaded" not in out


def test_install_names_an_interrupted_replacement(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    state, _ = paired
    agents = tmp_path / "LA"
    plan = plan_for(release, state)
    service.install_service(plan, runner=FakeLaunchctl(), launch_agents=agents, verify=False)
    fake = FakeLaunchctl(loaded=True, path=str(agents / TARGET), bootstrap_rc=5)
    with pytest.raises(ConfigError, match="previous service was already stopped"):
        service.install_service(plan, runner=fake, launch_agents=agents, verify=False)


@pytest.mark.parametrize("body", [plistlib.dumps(["not", "a", "dict"]), plistlib.dumps("text")])
def test_non_dict_plist_is_a_controlled_refusal(tmp_path, body):
    from orrery_chatgpt_mcp import service
    from orrery_chatgpt_mcp.config import ConfigError

    agents = tmp_path / "LA"
    agents.mkdir()
    (agents / TARGET).write_bytes(body)
    with pytest.raises(ConfigError, match="not a plist this command wrote"):
        service.uninstall_service(runner=FakeLaunchctl(), launch_agents=agents)


def test_bootstrap_has_no_tabs_so_launchctl_print_parsing_stays_unambiguous():
    from orrery_chatgpt_mcp import service

    assert "\t" not in service.BOOTSTRAP


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
        release = (tmp_path / "missing-release", release[1])
    agents = tmp_path / "LA"
    try:
        with pytest.raises(ConfigError):
            service.install_service(plan_for(release, state, **kw), runner=fake, launch_agents=agents, verify=False)
    finally:
        if holder:
            holder.close()
    if problem != "bootstrap":
        assert "bootstrap" not in fake.verbs()
        assert not (agents / "org.agentstack.chatgpt-mcp.plist").exists()


def test_uninstall_keeps_pairing(tmp_path, release, paired):
    from orrery_chatgpt_mcp import service

    state, _ = paired
    agents = tmp_path / "LA"
    fake = FakeLaunchctl()
    service.install_service(plan_for(release, state), runner=fake, launch_agents=agents, verify=False)
    assert service.uninstall_service(runner=fake, launch_agents=agents) is True
    assert fake.verbs()[-2:] == ["bootout", "print"]
    assert not (agents / "org.agentstack.chatgpt-mcp.plist").exists()
    assert (state / "pairing.json").exists()
    assert service.uninstall_service(runner=fake, launch_agents=agents) is False


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
    assert not (tmp_path / "state" / "releases").exists()
    assert _cli(["service", "install", "--bogus", "x"], {"ORRERY_MCP_STATE_DIR": str(tmp_path / "s")}, tmp_path).returncode == 2
    assert _cli(["service", "frobnicate"], {"ORRERY_MCP_STATE_DIR": str(tmp_path / "s")}, tmp_path).returncode == 2
