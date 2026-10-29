"""Keep the gateway running on macOS as a per-user launchd service.

The service runs a pinned, read-only copy of the gateway package (a
"release"), not the checkout it was installed from, so switching branches in
that checkout does not change what answers on the loopback port.

Startup is ``python -I -S -B -c BOOTSTRAP <release> <digest>``: no PYTHON*
variables, no site-packages, ``.pth`` files or ``sitecustomize``, no bytecode
writes. BOOTSTRAP checks the whole release layout (only the package's ``.py``
files and RELEASE.json, no caches, no symlinks, no writable entries, owned by
this user) and its digest before it imports anything from it. The digest is
part of the plist, so the job names the exact code it may run. The pairing
service needs only the standard library.

The plist holds no secret: the gateway reads the pairing secret from the 0600
state file at start, exactly as an interactive ``serve`` does. The service never enables spawning: it always sets ``ORRERY_MCP_ENABLE_SPAWN=0``.
Message sending is independently disabled by default. An operator may enable
only a previously authorized sender/allowlist with a pinned, verified helper
release; no Mail identity or recipient permissions are created by install.

Readiness and status probes send the pairing path only after ``lsof`` shows
that the probe's own TCP connection was accepted by the launchd job's process,
so another program that grabbed the port never receives the secret.

Nothing here touches Secure MCP Tunnel, tunnel-client or ChatGPT.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from . import pairing
from .config import ConfigError, GatewayConfig
from .helper_release import check_profile, verify_helper_release

LABEL = "org.agentstack.chatgpt-mcp"
PACKAGE = "orrery_chatgpt_mcp"
MIN_PYTHON = (3, 11)
PYTHON_FLAGS = ["-I", "-S", "-B"]
LSOF = "/usr/sbin/lsof"
BOOTOUT_POLLS = 50  # x 0.1 s for launchd to drop the job
# SHA-256 of the exact BOOTSTRAP written by the preceding release (PR #42).
PREVIOUS_BOOTSTRAP_SHA256 = "18c3da518e4c2ab6938b2e2d261683766df62750bac7a4270e969d956923fdda"

Runner = Callable[..., subprocess.CompletedProcess]

# The only implementation of the release check. It runs before anything from
# the release is imported, so it can use the standard library only. `--check`
# stops after the check (used by install and status).
BOOTSTRAP = r"""
import hashlib, os, stat, sys
root, want = sys.argv[1], sys.argv[2]
uid = os.getuid()
def bad(why):
    sys.stderr.write("orrery-chatgpt-mcp: refusing to start: release check failed: " + why + "\n")
    sys.exit(78)
def node(path, kind):
    try:
        st = os.lstat(path)
    except OSError:
        bad("missing " + os.path.basename(path))
    if not kind(st.st_mode) or st.st_uid != uid or st.st_mode & 0o222:
        bad("unsafe " + os.path.basename(path))
node(root, stat.S_ISDIR)
if sorted(os.listdir(root)) != ["RELEASE.json", "orrery_chatgpt_mcp"]:
    bad("unexpected entries in release")
node(os.path.join(root, "RELEASE.json"), stat.S_ISREG)
pkg = os.path.join(root, "orrery_chatgpt_mcp")
node(pkg, stat.S_ISDIR)
h = hashlib.sha256()
for name in sorted(os.listdir(pkg)):
    path = os.path.join(pkg, name)
    if not name.endswith(".py"):
        bad("unexpected " + name)
    node(path, stat.S_ISREG)
    with open(path, "rb") as fh:
        data = fh.read()
    rel = name.encode()
    h.update(len(rel).to_bytes(4, "big") + rel + len(data).to_bytes(8, "big") + data)
if h.hexdigest() != want:
    bad("digest mismatch")
if sys.argv[3:] == ["--check"]:
    print("ok")
    sys.exit(0)
sys.path.insert(0, root)
if os.environ.get("ORRERY_MCP_ENABLE_MESSAGE") == "1":
    from orrery_chatgpt_mcp.helper_release import verify_helper_release
    from pathlib import Path
    helper = os.environ.get("ORRERY_MCP_MESSAGE_HELPER_ROOT", "")
    helper_digest = os.environ.get("ORRERY_MCP_MESSAGE_HELPER_DIGEST", "")
    if not helper or not verify_helper_release(Path(helper), helper_digest):
        bad("message helper release integrity check failed")
from orrery_chatgpt_mcp.server import main
sys.exit(main(["serve"]))
"""


class VerifyError(Exception):
    """The service could not be shown to be the expected gateway. Messages are fixed labels only."""


@dataclass(frozen=True)
class ServicePlan:
    python: str
    release_dir: Path
    digest: str
    state_dir: Path
    port: int = 8790
    dashboard_url: str = "http://127.0.0.1:8770"
    label: str = LABEL
    message_profile: Path | None = None
    message_helper_root: Path | None = None
    message_helper_digest: str | None = None
    message_approval: str | None = None
    message_max_per_hour: int | None = None

    @property
    def log_path(self) -> Path:
        return self.state_dir / "gateway.log"


# -- pinned release ---------------------------------------------------------------

def _source_files(package_dir: Path) -> list[Path]:
    """The package's .py files. A source checkout may hold __pycache__; nothing else is allowed."""
    if not package_dir.is_dir() or package_dir.is_symlink():
        raise ConfigError(f"{package_dir} is not the {PACKAGE} package")
    files = []
    for path in sorted(package_dir.iterdir()):
        if path.name == "__pycache__" and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != ".py":
            raise ConfigError(f"unexpected entry in the gateway package: {path.name}")
        files.append(path)
    if package_dir / "__main__.py" not in files:
        raise ConfigError(f"{package_dir} is not the {PACKAGE} package")
    return files


def release_digest(package_dir: Path) -> str:
    """sha256 over the package's .py files (name and bytes); BOOTSTRAP computes the same."""
    h = hashlib.sha256()
    for path in _source_files(package_dir):
        rel = path.name.encode()
        data = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big") + rel + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def verify_release(release_dir: Path, digest: str, python: str | None = None, runner: Runner = subprocess.run) -> bool:
    """Run BOOTSTRAP's check (the same code launchd runs) without starting the gateway."""
    done = runner([python or sys.executable, *PYTHON_FLAGS, "-c", BOOTSTRAP, str(release_dir), digest, "--check"],
                  capture_output=True, text=True, timeout=60)
    return done.returncode == 0 and done.stdout.strip() == "ok"


def install_release(src_dir: Path, releases_root: Path, *, source_commit: str | None = None,
                    runner: Runner = subprocess.run) -> tuple[Path, str]:
    """Copy ``src_dir/orrery_chatgpt_mcp`` into a read-only release named by its digest.

    The staged copy is checked before it is published. An existing release
    with the same name is reused only if it passes the full check; anything
    else is refused rather than repaired.
    """
    package_dir = src_dir / PACKAGE
    digest = release_digest(package_dir)
    pairing._private_dir(releases_root)
    target = releases_root / digest[:16]
    if os.path.lexists(target):
        if not verify_release(target, digest, runner=runner):
            raise ConfigError(f"release {target} is modified or unsafe; remove it and install again")
        return target, digest
    tmp = releases_root / f".{digest[:16]}.{os.getpid()}.tmp"
    if os.path.lexists(tmp):
        raise ConfigError(f"{tmp} is left over from an interrupted install; remove it")
    (tmp / PACKAGE).mkdir(parents=True)
    for path in _source_files(package_dir):
        shutil.copyfile(path, tmp / PACKAGE / path.name, follow_symlinks=False)
    record = {"digest": digest, "source_commit": source_commit, "installed_at": int(time.time())}
    (tmp / "RELEASE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for path in [*(tmp / PACKAGE).iterdir(), tmp / "RELEASE.json", tmp / PACKAGE, tmp]:
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    if not verify_release(tmp, digest, runner=runner):
        raise ConfigError(f"the gateway package changed while it was copied; {tmp} was left for inspection")
    os.rename(tmp, target)
    return target, digest


def read_release(release_dir: Path) -> dict:
    """RELEASE.json as written at install time. Says nothing about integrity: use verify_release."""
    try:
        record = json.loads((release_dir / "RELEASE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


# -- launchd ---------------------------------------------------------------------

def program_arguments(plan: ServicePlan) -> list[str]:
    return [plan.python, *PYTHON_FLAGS, "-c", BOOTSTRAP, str(plan.release_dir), plan.digest]


def render_plist(plan: ServicePlan) -> bytes:
    """The LaunchAgent: no secrets, no spawn, explicit operator-only message opt-in."""
    env = {
        "ORRERY_MCP_AUTH": "pairing",
        "ORRERY_MCP_BIND_HOST": "127.0.0.1",
        "ORRERY_MCP_PORT": str(plan.port),
        "ORRERY_MCP_DASHBOARD_URL": plan.dashboard_url,
        "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
        "ORRERY_MCP_ENABLE_SPAWN": "0",
    }
    env["ORRERY_MCP_ENABLE_MESSAGE"] = "0"
    if plan.message_profile is not None:
        env.update({
            "ORRERY_MCP_ENABLE_MESSAGE": "1",
            "ORRERY_MCP_MESSAGE_PROFILE": str(plan.message_profile),
            "ORRERY_MCP_MESSAGE_HELPER_ROOT": str(plan.message_helper_root),
            "ORRERY_MCP_MESSAGE_HELPER_DIGEST": str(plan.message_helper_digest),
            "ORRERY_MCP_MESSAGE_APPROVAL": plan.message_approval or "chat",
            "ORRERY_MCP_MESSAGE_MAX_PER_HOUR": str(plan.message_max_per_hour if plan.message_max_per_hour is not None else 20),
        })
    return plistlib.dumps({
        "Label": plan.label,
        "ProgramArguments": program_arguments(plan),
        "WorkingDirectory": str(plan.release_dir),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(plan.log_path),
        "StandardErrorPath": str(plan.log_path),
    })


def plist_path(label: str = LABEL, launch_agents: Path | None = None) -> Path:
    return (launch_agents or Path.home() / "Library" / "LaunchAgents") / f"{label}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _load_own_plist(target: Path, label: str) -> dict | None:
    """The existing plist if it is one this module wrote; None if absent; refuse anything else."""
    if not os.path.lexists(target):
        return None
    try:
        if target.is_symlink():
            raise ValueError("symlink")
        with open(target, "rb") as fh:
            spec = plistlib.load(fh)
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise ConfigError(f"{target} is not a plist this command wrote; left in place") from None
    if not isinstance(spec, dict):
        raise ConfigError(f"{target} is not a plist this command wrote; left in place")
    args = spec.get("ProgramArguments")
    legacy = isinstance(args, list) and args[1:] == ["-s", "-m", PACKAGE, "serve"]
    pinned = isinstance(args, list) and len(args) == 8 and args[1:5] == [*PYTHON_FLAGS, "-c"] and isinstance(args[5], str)
    current = pinned and (args[5] == BOOTSTRAP or
                          hashlib.sha256(args[5].encode("utf-8")).hexdigest() == PREVIOUS_BOOTSTRAP_SHA256)
    if spec.get("Label") != label or not (legacy or current):
        raise ConfigError(f"{target} is not a plist this command wrote; left in place")
    return spec


def _check_python(python: str, runner: Runner) -> None:
    if not os.path.isabs(python) or not os.access(python, os.X_OK):
        raise ConfigError(f"python must be an absolute path to an executable: {python}")
    probe = runner([python, *PYTHON_FLAGS, "-c", "import sys; print(sys.version_info[:2] >= %r)" % (MIN_PYTHON,)],
                   capture_output=True, text=True, timeout=30)
    if probe.returncode != 0 or probe.stdout.strip() != "True":
        raise ConfigError(f"{python} is not Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


LAUNCHCTL_NO_SUCH_SERVICE = 113


def job_info(label: str = LABEL, runner: Runner = subprocess.run) -> dict | None:
    """What launchd reports for the job: None only when launchd says it does not exist.

    Any other failure to inspect launchd raises ConfigError, so that an error is
    never mistaken for "not loaded".
    """
    done = runner(["launchctl", "print", f"{_domain()}/{label}"], capture_output=True, text=True)
    if done.returncode == LAUNCHCTL_NO_SUCH_SERVICE and "Could not find service" in (done.stderr or ""):
        return None
    if done.returncode != 0:
        raise ConfigError(f"launchctl print failed ({done.returncode}); cannot tell whether {label} is loaded")
    path = re.search(r"^\tpath = (.+)$", done.stdout or "", re.M)
    pid = re.search(r"^\tpid = (\d+)$", done.stdout or "", re.M)
    return {"path": path.group(1) if path else None, "pid": int(pid.group(1)) if pid else None}


def is_loaded(label: str = LABEL, runner: Runner = subprocess.run) -> bool:
    return job_info(label, runner) is not None


def job_pid(label: str = LABEL, runner: Runner = subprocess.run) -> int | None:
    info = job_info(label, runner)
    return info["pid"] if info else None


def _require_loaded_from(info: dict, target: Path, label: str) -> None:
    """Refuse to stop a job that launchd did not load from our plist."""
    if info.get("path") != str(target):
        raise ConfigError(f"{label} is loaded from {info.get('path') or 'an unknown place'}, not {target}; not touching it")


def _bootout(label: str, runner: Runner) -> None:
    done = runner(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True, text=True)
    for _ in range(BOOTOUT_POLLS):
        if job_info(label, runner) is None:  # inspection errors propagate
            return
        time.sleep(0.1)
    raise ConfigError(f"launchctl bootout {label} failed ({done.returncode}); the service is still loaded")


def _accepted_by(sock: socket.socket, port: int, pid: int, runner: Runner) -> bool:
    """True when process ``pid`` holds the server end of this very connection."""
    lport = sock.getsockname()[1]
    want = f"n127.0.0.1:{port}->127.0.0.1:{lport}"
    for _ in range(20):
        done = runner([LSOF, "-nP", "-a", "-p", str(pid), f"-iTCP@127.0.0.1:{lport}", "-Fn"],
                      capture_output=True, text=True, timeout=10)
        if done.returncode == 0 and want in done.stdout.splitlines():
            return True
        time.sleep(0.1)
    return False


def probe_status(port: int, secret: str, pid: int, runner: Runner = subprocess.run, timeout: float = 5) -> dict:
    """Call orrery_status over loopback the way tunnel-client would, after checking who answers.

    Raises VerifyError with a fixed label; nothing from the peer or the secret
    reaches the message.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    stage = "connect"
    try:
        conn.connect()
        stage = "peer check"
        if not _accepted_by(conn.sock, port, pid, runner):
            raise VerifyError("the listener is not the service process")
        stage = "orrery_status"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "orrery_status", "arguments": {}}})
        conn.request("POST", f"/mcp/{secret}", body=body,
                     headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                              "Accept": "application/json, text/event-stream"})
        resp = conn.getresponse()
        data = resp.read(1 << 20)
        if resp.status != 200:
            raise VerifyError(f"gateway answered HTTP {int(resp.status)}")
        status = json.loads(data)["result"]["structuredContent"]
        ok = (status["gateway"]["name"] == "orrery-chatgpt-mcp" and status["connection"]["auth_mode"] == "pairing"
              and status["capabilities"]["spawn_enabled_on_server"] is False)
    except VerifyError:
        raise
    except Exception as exc:  # noqa: BLE001 - anything else becomes a fixed label
        raise VerifyError(f"probe failed during {stage} ({type(exc).__name__})") from None
    finally:
        conn.close()
    if not ok:
        raise VerifyError("the gateway does not have the service configuration")
    return status


def wait_until_answering(plan: ServicePlan, secret: str, runner: Runner = subprocess.run, deadline: float = 30) -> dict:
    end = time.monotonic() + deadline
    last = VerifyError("the service did not start")
    while time.monotonic() < end:
        pid = job_pid(plan.label, runner)
        if pid:
            try:
                return probe_status(plan.port, secret, pid, runner)
            except VerifyError as exc:
                last = exc
        time.sleep(0.5)
    raise last


def _preflight_message_sender(plan: ServicePlan) -> None:
    """Read-only canonical identity/recipient verification before replacing a working service."""
    from .messaging import DaemonSender, SenderError
    sender = DaemonSender(plan.message_profile, plan.message_helper_root)
    try:
        allowed = sender.allowed()
        with sender.session() as runtime:
            runtime.identity()
            for name, mail_id in allowed.items():
                recipient = runtime.lookup(name)
                if recipient["id"] != mail_id or recipient["retired"]:
                    raise ConfigError("sender allowlist includes a changed or retired recipient")
    except SenderError as exc:
        raise ConfigError(f"message sender cannot be verified ({exc.code}); old service left running") from None


def _resolve_message_controls(plan: ServicePlan, existing: dict | None) -> ServicePlan:
    """Preserve stricter existing operator controls unless explicitly overridden."""
    if plan.message_profile is None:
        return plan
    env = (existing or {}).get("EnvironmentVariables", {})
    if not isinstance(env, dict) or env.get("ORRERY_MCP_ENABLE_MESSAGE") != "1":
        env = {}
    approval = (plan.message_approval if plan.message_approval is not None else
                env.get("ORRERY_MCP_MESSAGE_APPROVAL", "chat"))
    value = (plan.message_max_per_hour if plan.message_max_per_hour is not None else
             env.get("ORRERY_MCP_MESSAGE_MAX_PER_HOUR", 20))
    try:
        rate = int(value)
    except (ValueError, TypeError):
        raise ConfigError("existing message max per hour is not an integer") from None
    return replace(plan, message_approval=approval, message_max_per_hour=rate)


def install_service(plan: ServicePlan, *, runner: Runner = subprocess.run, launch_agents: Path | None = None,
                    verify: bool = True) -> dict:
    """Write the LaunchAgent and (re)load it. Refuses instead of repairing a bad setup.

    Raises ConfigError when it refuses or a step fails (the message says
    whether the previous job had already been stopped), and VerifyError when
    the new job was loaded but could not be shown to answer as the expected
    gateway.
    """
    secret = pairing.read_secret(plan.state_dir)  # no pairing yet → refuse
    GatewayConfig(pairing_secret=secret, port=plan.port, dashboard_url=plan.dashboard_url,
                  state_dir=plan.state_dir).validate()
    _check_python(plan.python, runner)
    if not verify_release(plan.release_dir, plan.digest, plan.python, runner):
        raise ConfigError(f"{plan.release_dir} is not an intact release")
    target = plist_path(plan.label, launch_agents)
    existing = _load_own_plist(target, plan.label)
    plan = _resolve_message_controls(plan, existing)
    if plan.message_profile is not None:
        if not (plan.release_dir / PACKAGE / "helper_release.py").is_file():
            raise ConfigError("message opt-in requires a gateway release with helper support")
        if not plan.message_helper_root or not plan.message_helper_digest:
            raise ConfigError("message service needs a pinned helper release")
        if plan.message_approval not in ("chat", "local") or not 1 <= plan.message_max_per_hour <= 200:
            raise ConfigError("invalid message service approval or hourly rate")
        if not verify_helper_release(plan.message_helper_root, plan.message_helper_digest):
            raise ConfigError("message helper release is not intact")
        check_profile(plan.message_profile)
        _preflight_message_sender(plan)
    elif plan.message_helper_root or plan.message_helper_digest:
        raise ConfigError("message helper is set without an approved profile")
    info = job_info(plan.label, runner)
    stopped = ""
    if info is not None:
        _require_loaded_from(info, target, plan.label)
        if existing is None:
            raise ConfigError(f"{plan.label} is loaded but {target} is gone; run `service uninstall` first")
        _bootout(plan.label, runner)
        stopped = " (the previous service was already stopped)"
        for _ in range(50):
            if not _port_in_use(plan.port):
                break
            time.sleep(0.2)
    if _port_in_use(plan.port):
        raise ConfigError(f"127.0.0.1:{plan.port} is already in use by another program{stopped}")
    try:
        # launchd opens StandardErrorPath before applying Umask; create the log 0600 first.
        os.close(os.open(plan.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600))
        os.chmod(plan.log_path, 0o600)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_bytes(render_plist(plan))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except OSError as exc:
        raise ConfigError(f"could not write {exc.filename or 'the service files'}: {exc.strerror}{stopped}") from None
    done = runner(["launchctl", "bootstrap", _domain(), str(target)], capture_output=True, text=True)
    if done.returncode != 0:
        raise ConfigError(f"launchctl bootstrap failed ({done.returncode}): {done.stderr.strip()[:300]}{stopped}")
    if not verify:
        return {}
    status = wait_until_answering(plan, secret, runner)
    if status["capabilities"]["message_enabled_on_server"] is not bool(plan.message_profile):
        raise VerifyError("service message capability does not match explicit installation")
    return status


def uninstall_service(label: str = LABEL, *, runner: Runner = subprocess.run, launch_agents: Path | None = None) -> bool:
    """Stop and remove the LaunchAgent. Keeps the pairing, approvals, ledger and releases.

    The plist is checked before anything is stopped, and removed only after
    launchd confirms the job is gone.
    """
    target = plist_path(label, launch_agents)
    existing = _load_own_plist(target, label)
    info = job_info(label, runner)
    loaded = info is not None
    if loaded:
        _require_loaded_from(info, target, label)
        _bootout(label, runner)
    if existing is not None:
        target.unlink()
        return True
    return loaded


def service_status(label: str = LABEL, *, runner: Runner = subprocess.run, launch_agents: Path | None = None) -> dict:
    target = plist_path(label, launch_agents)
    out: dict = {"label": label, "plist": os.path.lexists(target)}
    try:
        info = job_info(label, runner)
        spec = _load_own_plist(target, label)
    except ConfigError as exc:
        out["error"] = str(exc)
        return out
    pid = info["pid"] if info else None
    out.update(loaded=info is not None, running=bool(pid))
    if info is not None and info.get("path") != str(target):
        out["error"] = f"the loaded job comes from {info.get('path')}"
        return out
    if spec is None:
        return out
    env = spec.get("EnvironmentVariables", {})
    args = spec["ProgramArguments"]
    if len(args) == 8:
        release, digest = Path(args[6]), args[7]
    else:  # plist from the first version of this command
        release = Path(env.get("PYTHONPATH", ""))
        digest = str(read_release(release).get("digest", ""))
    enabled = env.get("ORRERY_MCP_ENABLE_MESSAGE") == "1"
    out.update(message=enabled)
    if enabled:
        out["message_helper_intact"] = verify_helper_release(Path(env.get("ORRERY_MCP_MESSAGE_HELPER_ROOT", "")),
                                                               env.get("ORRERY_MCP_MESSAGE_HELPER_DIGEST", ""))
    out.update(port=int(env.get("ORRERY_MCP_PORT", "8790")), spawn=env.get("ORRERY_MCP_ENABLE_SPAWN") == "1",
               python=args[0], release=str(release), source_commit=read_release(release).get("source_commit"),
               release_intact=bool(digest) and verify_release(release, digest, args[0], runner))
    if not pid:
        out["answering"] = False
        return out
    try:
        status = probe_status(out["port"], pairing.read_secret(Path(env.get("ORRERY_MCP_STATE_DIR", ""))), pid, runner)
        out["answering"] = True
        out["dashboard_reachable"] = bool(status.get("dashboard", {}).get("reachable"))
    except VerifyError as exc:
        out["answering"] = False
        out["error"] = str(exc)
    except ConfigError:
        out["answering"] = False
        out["error"] = "pairing unreadable"
    return out


def default_python() -> str:
    return os.environ.get("AGENTSTACK_PYTHON", "").strip() or sys.executable
