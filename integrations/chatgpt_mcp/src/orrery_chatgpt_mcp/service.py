"""Keep the gateway running on macOS as a per-user launchd service.

The service runs a pinned, read-only copy of the gateway package (a
"release"), not the checkout it was installed from, so switching branches in
that checkout does not change what answers on the loopback port. The plist
holds no secret: the gateway reads the pairing secret from the 0600 state file
at start, exactly as an interactive ``serve`` does. The service is read-only:
it always sets ``ORRERY_MCP_ENABLE_SPAWN=0``. Launches stay an interactive
``serve`` choice.

Nothing here touches Secure MCP Tunnel, tunnel-client or ChatGPT.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import plistlib
import shutil
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import pairing
from .config import ConfigError, GatewayConfig

LABEL = "org.agentstack.chatgpt-mcp"
PACKAGE = "orrery_chatgpt_mcp"
MIN_PYTHON = (3, 11)

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class ServicePlan:
    python: str
    release_dir: Path
    state_dir: Path
    port: int = 8790
    dashboard_url: str = "http://127.0.0.1:8770"
    label: str = LABEL

    @property
    def log_path(self) -> Path:
        return self.state_dir / "gateway.log"


# -- pinned release ---------------------------------------------------------------

def _package_files(package_dir: Path) -> list[Path]:
    files = []
    for path in sorted(package_dir.rglob("*")):
        if "__pycache__" in path.relative_to(package_dir).parts:
            continue
        if path.is_symlink():
            raise ConfigError(f"refusing a symlink in the gateway package: {path}")
        if path.is_file() and path.suffix == ".py":
            files.append(path)
    if not (package_dir / "__main__.py") in files:
        raise ConfigError(f"{package_dir} is not the {PACKAGE} package")
    return files


def release_digest(package_dir: Path) -> str:
    """sha256 over the package's .py files (relative path and bytes)."""
    h = hashlib.sha256()
    for path in _package_files(package_dir):
        rel = path.relative_to(package_dir).as_posix().encode()
        data = path.read_bytes()
        h.update(len(rel).to_bytes(4, "big") + rel + len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def install_release(src_dir: Path, releases_root: Path, *, source_commit: str | None = None) -> Path:
    """Copy ``src_dir/orrery_chatgpt_mcp`` into a read-only release named by its digest.

    An existing release with the same name is reused only if its contents still
    match; a modified release is refused rather than repaired.
    """
    package_dir = src_dir / PACKAGE
    digest = release_digest(package_dir)
    pairing._private_dir(releases_root)
    target = releases_root / digest[:16]
    if target.exists():
        if release_digest(target / PACKAGE) != digest:
            raise ConfigError(f"release {target} was modified; remove it and install again")
        return target
    tmp = releases_root / f".{digest[:16]}.{os.getpid()}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / PACKAGE).mkdir(parents=True)
    for path in _package_files(package_dir):
        dest = tmp / PACKAGE / path.relative_to(package_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    record = {"digest": digest, "source_commit": source_commit, "installed_at": int(time.time())}
    (tmp / "RELEASE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for path in sorted(tmp.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(tmp, 0o555)
    os.rename(tmp, target)
    return target


def read_release(release_dir: Path) -> dict:
    try:
        record = json.loads((release_dir / "RELEASE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"intact": False}
    record["intact"] = release_digest(release_dir / PACKAGE) == record.get("digest")
    return record


# -- launchd ---------------------------------------------------------------------

def render_plist(plan: ServicePlan) -> bytes:
    """The LaunchAgent. No secret and no spawn: see the module docstring."""
    env = {
        "PYTHONPATH": str(plan.release_dir),
        "PYTHONSAFEPATH": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "ORRERY_MCP_AUTH": "pairing",
        "ORRERY_MCP_BIND_HOST": "127.0.0.1",
        "ORRERY_MCP_PORT": str(plan.port),
        "ORRERY_MCP_DASHBOARD_URL": plan.dashboard_url,
        "ORRERY_MCP_STATE_DIR": str(plan.state_dir),
        "ORRERY_MCP_ENABLE_SPAWN": "0",
    }
    return plistlib.dumps({
        "Label": plan.label,
        "ProgramArguments": [plan.python, "-s", "-m", PACKAGE, "serve"],
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


def _check_python(python: str, runner: Runner) -> None:
    if not os.path.isabs(python) or not os.access(python, os.X_OK):
        raise ConfigError(f"python must be an absolute path to an executable: {python}")
    probe = runner([python, "-I", "-c", "import sys; print(sys.version_info[:2] >= %r)" % (MIN_PYTHON,)],
                   capture_output=True, text=True, timeout=30)
    if probe.returncode != 0 or probe.stdout.strip() != "True":
        raise ConfigError(f"{python} is not Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_loaded(label: str = LABEL, runner: Runner = subprocess.run) -> bool:
    return runner(["launchctl", "print", f"{_domain()}/{label}"], capture_output=True, text=True).returncode == 0


def probe_status(port: int, secret: str, timeout: float = 5) -> dict:
    """Call orrery_status over loopback the way tunnel-client would. Never prints the secret."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "orrery_status", "arguments": {}}})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", f"/mcp/{secret}", body=body,
                     headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                              "Accept": "application/json, text/event-stream"})
        resp = conn.getresponse()
        data = resp.read(1 << 20)
    finally:
        conn.close()
    if resp.status != 200:
        raise ConnectionError(f"gateway answered HTTP {resp.status}")
    return json.loads(data)["result"]["structuredContent"]


def wait_until_answering(port: int, secret: str, deadline: float = 30) -> dict:
    end = time.monotonic() + deadline
    while True:
        try:
            return probe_status(port, secret)
        except (OSError, ValueError, KeyError, ConnectionError):
            if time.monotonic() >= end:
                raise
            time.sleep(0.5)


def install_service(plan: ServicePlan, *, runner: Runner = subprocess.run, launch_agents: Path | None = None,
                    verify: bool = True) -> dict:
    """Write the LaunchAgent and (re)load it. Refuses instead of repairing a bad setup."""
    secret = pairing.read_secret(plan.state_dir)  # no pairing yet → refuse
    GatewayConfig(pairing_secret=secret, port=plan.port, dashboard_url=plan.dashboard_url,
                  state_dir=plan.state_dir).validate()
    _check_python(plan.python, runner)
    if not read_release(plan.release_dir).get("intact"):
        raise ConfigError(f"{plan.release_dir} is not an intact release")
    target = plist_path(plan.label, launch_agents)
    if is_loaded(plan.label, runner):
        runner(["launchctl", "bootout", f"{_domain()}/{plan.label}"], capture_output=True, text=True)
        for _ in range(50):
            if not _port_in_use(plan.port):
                break
            time.sleep(0.2)
    if _port_in_use(plan.port):
        raise ConfigError(f"127.0.0.1:{plan.port} is already in use by another program")
    # launchd opens StandardErrorPath before applying Umask; create the log 0600 first.
    os.close(os.open(plan.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600))
    os.chmod(plan.log_path, 0o600)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_bytes(render_plist(plan))
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    done = runner(["launchctl", "bootstrap", _domain(), str(target)], capture_output=True, text=True)
    if done.returncode != 0:
        raise ConfigError(f"launchctl bootstrap failed ({done.returncode}): {done.stderr.strip()[:300]}")
    return wait_until_answering(plan.port, secret) if verify else {}


def uninstall_service(label: str = LABEL, *, runner: Runner = subprocess.run, launch_agents: Path | None = None) -> bool:
    """Stop and remove the LaunchAgent. Keeps the pairing, approvals, ledger and releases."""
    target = plist_path(label, launch_agents)
    loaded = is_loaded(label, runner)
    if loaded:
        runner(["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True, text=True)
    if target.exists():
        with open(target, "rb") as fh:
            if plistlib.load(fh).get("Label") != label:
                raise ConfigError(f"{target} is not this service's plist; left in place")
        target.unlink()
        return True
    return loaded


def service_status(label: str = LABEL, *, runner: Runner = subprocess.run, launch_agents: Path | None = None) -> dict:
    target = plist_path(label, launch_agents)
    out: dict = {"label": label, "plist": target.exists(), "loaded": is_loaded(label, runner)}
    if out["plist"]:
        with open(target, "rb") as fh:
            spec = plistlib.load(fh)
        env = spec.get("EnvironmentVariables", {})
        release = Path(env.get("PYTHONPATH", ""))
        out.update(port=int(env.get("ORRERY_MCP_PORT", "8790")), spawn=env.get("ORRERY_MCP_ENABLE_SPAWN") == "1",
                   release=str(release), python=spec.get("ProgramArguments", [""])[0])
        info = read_release(release)
        out.update(release_intact=info.get("intact"), source_commit=info.get("source_commit"))
        try:
            secret = pairing.read_secret(Path(env.get("ORRERY_MCP_STATE_DIR", "")))
            status = probe_status(out["port"], secret)
            out["answering"] = True
            out["dashboard_reachable"] = bool(status.get("dashboard", {}).get("reachable"))
        except (OSError, ValueError, KeyError, ConnectionError, ConfigError) as exc:
            out["answering"] = False
            out["error"] = type(exc).__name__
    return out


def default_python() -> str:
    return os.environ.get("AGENTSTACK_PYTHON", "").strip() or sys.executable
