"""Private lifetime channel: the supervisor, not the runner, owns native children."""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import runpy
import select
import signal
import socket
import subprocess
import sys
import time

from .fresh_antigravity import read_plan
from .fresh_launch import identity
from .fresh_runtime import _write_private
from .storage_origin_contract import B1Error


def _diag(message):
    try:
        print(message, file=sys.stderr)
    except OSError:
        pass


def _owner_argv(plan_path, *args):
    # The worktree may contain modules shadowing the stdlib or this package.
    # Python >=3.11 supports safe-path mode; use a trusted cwd for every
    # interpreter in the ownership chain, then chdir only for native exec.
    return [sys.executable, "-P", "-m", "agentstack_mail.fresh_antigravity_supervisor",
            "--plan", str(Path(plan_path).resolve()), *args]


class NativeOwner:
    def __init__(self, plan_path, runner_pid):
        self.client, self.plan, self.run, _ = read_plan(Path(plan_path))
        self.state = self.run / "antigravity"
        self.expected = identity(self.client)
        self.runner_pid = runner_pid
        self.process = self.native_record = self.native_code = None
        self.containment_witness = None
        self.processes = runpy.run_path(str(Path(self.plan["wrapper_root"])
                                           / "bin/lib/process_observation.py"))

    def capture_native(self, *, publish=True):
        pid = self.process.pid
        birth = self.processes["process_start"](pid)
        rows = self.processes["process_snapshot"](include_groups=True)
        row = rows.get(pid) if isinstance(rows, dict) else None
        if (birth in (None, "absent") or row != (os.getpid(), os.getuid(), birth, pid)
                or os.getpgid(pid) != pid or pid == os.getpgrp()):
            raise B1Error("FRESH_ANTIGRAVITY_PROCESS_UNCONFIRMED")
        self.native_record = dict(pid=pid, process_start=birth, pgid=pid,
            runner_pid=self.runner_pid, launch_id=self.run.name,
            context_digest=self.client.context_digest, **self.expected)
        if publish:
            _write_private(self.state / "native-process.json", self.native_record)

    def native_group(self, deadline):
        record = self.native_record
        if record is None:
            raise B1Error("FRESH_ANTIGRAVITY_PROCESS_UNCONFIRMED")
        rows = self.processes["process_snapshot"](include_groups=True, deadline=deadline)
        if not isinstance(rows, dict) or any(len(row) != 4 for row in rows.values()):
            raise B1Error("FRESH_ANTIGRAVITY_PROCESS_UNCONFIRMED")
        leader = rows.get(record["pid"])
        if (leader != (os.getpid(), os.getuid(), record["process_start"], record["pgid"])
                or record["pgid"] != self.process.pid or record["pgid"] == os.getpgrp()):
            raise B1Error("FRESH_ANTIGRAVITY_PROCESS_CHANGED")
        group = {pid: row for pid, row in rows.items() if row[3] == record["pgid"]}
        if any(row[1] != os.getuid() for row in group.values()):
            raise B1Error("FRESH_ANTIGRAVITY_PROCESS_CHANGED")
        # A still-related descendant can leave the process group (e.g. setsid).
        # Persist a fail-closed witness before sending any group signal.
        # Never signal a process outside the anchored group automatically.
        escaped = {pid: row for pid, row in rows.items() if pid not in group
                   and self.processes["snapshot_belongs_to"](
                       rows, pid, record["pid"], os.getuid())}
        if escaped and self.containment_witness is None:
            self.containment_witness = {"native": record, "escaped": [
                {"pid": pid, "process_start": row[2], "pgid": row[3]}
                for pid, row in sorted(escaped.items())]}
        # A failed publication must not become a clean observation when the
        # descendant disappears or reparents before the next cleanup retry.
        if self.containment_witness is not None:
            marker = self.state / "native-containment-lost.json"
            try:
                _write_private(marker, self.containment_witness)
            except FileExistsError:
                pass  # Presence alone blocks closure pending explicit recovery.
        return group

    def reap_native_if_ended(self, deadline):
        if set(self.native_group(deadline)) != {self.process.pid}:
            return False
        remaining = max(0.01, min(1, deadline - time.monotonic()))
        state = subprocess.run(["/bin/ps", "-p", str(self.process.pid), "-o", "stat="],
            capture_output=True, text=True, timeout=remaining, check=False)
        if state.returncode != 0 or not state.stdout.strip():
            raise B1Error("FRESH_ANTIGRAVITY_PROCESS_UNCONFIRMED")
        if not state.stdout.strip().startswith("Z"):
            return False
        if set(self.native_group(deadline)) != {self.process.pid}:
            return False
        self.native_code = self.process.wait(timeout=1)
        return True

    def stop_native(self):
        if self.process is None:
            return
        if self.native_record is None:
            self.capture_native(publish=False)
        # Keep the unreaped child PID as the group identity anchor until every
        # other member is gone. No Popen.poll()/wait() runs before that point.
        for signum in (signal.SIGTERM, signal.SIGKILL):
            if self.reap_native_if_ended(time.monotonic() + 3):
                return
            self.native_group(time.monotonic() + 3)
            try:
                os.killpg(self.native_record["pgid"], signum)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                # Darwin can reject a group containing only an exited leader.
                if error.errno != errno.EPERM or not self.reap_native_if_ended(time.monotonic() + 3):
                    raise
                return
            # The phase deadline controls waiting, not the observation timeout:
            # a last sample with a nearly exhausted budget must not prevent
            # escalation after a fresh, independently bounded identity check.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.reap_native_if_ended(time.monotonic() + 3):
                    return
                time.sleep(0.05)
        raise B1Error("FRESH_ANTIGRAVITY_GROUP_ALIVE")


def _receive(connection):
    data = bytearray()
    while len(data) < 4096:
        part = connection.recv(1)
        if not part:
            break
        if part == b"\n":
            value = json.loads(data)
            if isinstance(value, dict):
                return value
            break
        data.extend(part)
    raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")


def _send(connection, value):
    connection.sendall(json.dumps(value).encode() + b"\n")


class SupervisedNative:
    def __init__(self):
        self.worker = self.control = self.stdin = self.stdout = self.pid = None
        self.ready = False

    def start(self, plan_path):
        self.control, peer = socket.socketpair()
        try:
            self.worker = subprocess.Popen(
                _owner_argv(plan_path, "--runner", str(os.getpid()),
                            "--control-fd", str(peer.fileno())),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                cwd="/", start_new_session=True, pass_fds=(peer.fileno(),))
            self.stdin, self.stdout = self.worker.stdin, self.worker.stdout
        finally:
            peer.close()
        self.control.settimeout(30)
        value = _receive(self.control)
        if set(value) != {"pid"} or type(value["pid"]) is not int or value["pid"] < 1:
            raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")
        self.pid = value["pid"]
        self.ready = True

    def stop(self):
        try:
            if self.control is None or self.worker is None:
                raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")
            try:
                self.control.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            if not self.ready:
                self.worker.wait(timeout=40)
                raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")
            self.control.settimeout(40)
            value = _receive(self.control)
            if value == {"error": "FRESH_ANTIGRAVITY_GUARD_LOST"}:
                self.worker.wait(timeout=5)
                raise B1Error("FRESH_ANTIGRAVITY_GUARD_LOST")
            if (set(value) != {"native_code"} or type(value["native_code"]) is not int
                    or self.worker.wait(timeout=5) != 0):
                raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")
            return value["native_code"]
        finally:
            if self.control is not None:
                self.control.close()


def runner_present(connection):
    connection.setblocking(False)
    try:
        # The runner never writes application data; any read readiness means stop.
        connection.recv(1, socket.MSG_PEEK)
        return False
    except BlockingIOError:
        return True
    finally:
        connection.setblocking(True)


def guard_native_group(plan_path, fd, ready, leader):
    """The guard is itself a member of the private native session/group.

    Its own live membership anchors the group even if the supervisor dies.
    It never accepts a target group from disk or signals another group.
    """
    if (leader <= 1 or os.getpid() == leader or os.getpgrp() != leader
            or os.getsid(0) != leader or os.getppid() != leader):
        return 1
    read_plan(Path(plan_path))
    stopping = False

    # A native group can receive a terminal hangup while the guard is still
    # required to anchor its identity. Only the private owner channel ends it.
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, signal.SIG_IGN)
    try:
        if select.select([fd], [], [], 0)[0]:
            return 1
        os.write(ready, b"1")
    finally:
        os.close(ready)
    try:
        while True:
            if select.select([fd], [], [], 0.25)[0]:
                # Only the private owner channel may authorize departure.
                # Group SIGTERM (including provider-originated signals) cannot.
                if os.read(fd, 1) == b"S":
                    stopping = True
                else:
                    if os.getpgrp() != leader or os.getsid(0) != leader:
                        return 1
                    os.killpg(leader, signal.SIGKILL)
                    return 1
            if not stopping:
                continue
            # A normal owner shutdown can reap the leader only once we have
            # left. Exit only when the leader is a zombie and there are no
            # other group members capable of forking.
            try:
                state = subprocess.run(["/bin/ps", "-A", "-o", "pid=,pgid=,stat="],
                    capture_output=True, text=True, timeout=2, check=False,
                    start_new_session=True)
                if state.returncode != 0:
                    continue
                rows = {}
                for line in state.stdout.splitlines():
                    parts = line.split()
                    if len(parts) != 3 or not all(v.isdigit() for v in parts[:2]):
                        raise ValueError("invalid process observation")
                    pid, pgid = map(int, parts[:2])
                    if pid in rows:
                        raise ValueError("duplicate process observation")
                    rows[pid] = (pgid, parts[2])
                members = {pid for pid, row in rows.items() if row[0] == leader}
                if members == {leader, os.getpid()} and rows[leader][1].startswith("Z"):
                    return 0
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                continue
    finally:
        os.close(fd)


def gated_native(plan_path, fd, watch_fd=None):
    # Until the owner has captured and published the identity, this trusted
    # single process cannot start the provider or create any descendants.
    try:
        if os.read(fd, 1) != b"1":
            return 1
    finally:
        os.close(fd)
    _, plan, _, _ = read_plan(Path(plan_path))
    if watch_fd is None:
        raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")
    if watch_fd is not None:
        read_ready, write_ready = os.pipe()
        try:
            try:
                subprocess.Popen(
                    _owner_argv(plan_path, "--guardian-fd", str(watch_fd),
                                "--ready-fd", str(write_ready), "--leader", str(os.getpid())),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=None,
                    cwd="/", pass_fds=(watch_fd, write_ready))
            finally:
                os.close(write_ready)
                os.close(watch_fd)
            if not select.select([read_ready], [], [], 10)[0] or os.read(read_ready, 1) != b"1":
                raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_UNCONFIRMED")
        finally:
            os.close(read_ready)
    os.chdir(plan["cwd"])
    os.execv(plan["argv"][0], plan["argv"])


def finish_released_native(owner, *, sleep=time.sleep):
    # Do not discard the unreaped ownership anchor on an inconclusive process
    # observation. The runner may time out, but the supervisor continues to
    # hold the native child and only signals after a fresh identity check.
    # Persistent ambiguity requires operator investigation; never claim ended.
    failures = 0
    while True:
        try:
            owner.stop_native()
            return
        except Exception as error:
            failures += 1
            if failures == 1 or failures % 12 == 0:
                reason = error.code if isinstance(error, B1Error) else type(error).__name__
                _diag("FRESH ANTIGRAVITY SUPERVISOR CLEANUP PENDING: " + reason)
            sleep(min(5, failures))


def supervise(plan_path, runner_pid, connection):
    owner = None
    released = False
    code = 1
    interrupted = False
    watch_owner = None
    guard_lost = False

    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
        # Wake a blocking control read without interrupting Popen or capture.
        try:
            connection.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupt)
    try:
        if os.getppid() != runner_pid or runner_pid <= 1:
            return 1
        owner = NativeOwner(plan_path, runner_pid)
        if interrupted or not runner_present(connection):
            return 1
        watch_owner, watch_peer = socket.socketpair()
        try:
            gate_read, gate_write = os.pipe()
        except OSError:
            watch_peer.close()
            raise
        try:
            try:
                owner.process = subprocess.Popen(
                    _owner_argv(plan_path, "--gate-fd", str(gate_read),
                                "--watch-fd", str(watch_peer.fileno())),
                    stdin=None, stdout=None, stderr=None, cwd="/",
                    start_new_session=True, pass_fds=(gate_read, watch_peer.fileno()))
            finally:
                os.close(gate_read)
                watch_peer.close()
            # The native process has its own copies. Do not keep the runner's
            # stdin/stdout pipes open after native exit.
            with open(os.devnull, "rb+") as sink:
                os.dup2(sink.fileno(), 0)
                os.dup2(sink.fileno(), 1)
            owner.capture_native()
            if interrupted or not runner_present(connection):
                return 1
            # Treat even a failed write as a possibly released gate; use the
            # verified group cleanup path in that case.
            released = True
            os.write(gate_write, b"1")
        finally:
            os.close(gate_write)
        _send(connection, {"pid": owner.process.pid})
        # The independent guard and the supervisor watch the same private
        # lifetime socket in opposite directions. Only explicit stop is sent.
        ready, _, _ = select.select([connection, watch_owner], [], [])
        guard_lost = watch_owner in ready
        if not guard_lost:
            connection.recv(1)
    except (B1Error, OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        _diag("FRESH ANTIGRAVITY SUPERVISOR: " + (error.code if isinstance(error, B1Error) else type(error).__name__))
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, signal.SIG_IGN)
        if owner is not None and owner.process is not None:
            try:
                if released:
                    if not guard_lost:
                        try:
                            watch_owner.sendall(b"S")
                        except OSError:
                            guard_lost = True
                    finish_released_native(owner)
                else:
                    # Gate EOF: no provider could have executed. Reap only
                    # our direct child, whose PID cannot be reused yet.
                    try:
                        owner.native_code = owner.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        owner.process.kill()
                        owner.native_code = owner.process.wait(timeout=5)
                # An unexpected guard exit is not a normal provider exit.
                # Keep reservations until the existing explicit resolution path
                # proves termination; do not publish a normal cleanup receipt.
                # Publish a positive cleanup receipt before acknowledging stop.
                # Missing/failed publication, guard loss or an observed escape
                # cannot be converted into a clean group-empty observation.
                if (not guard_lost and owner.native_record is not None
                        and owner.containment_witness is None):
                    from .fresh_antigravity_end import cleanup_receipt
                    _write_private(owner.state / "native-cleanup.json",
                                   cleanup_receipt(owner.native_record))
                code = 1 if guard_lost else 0
                _send(connection, {"error": "FRESH_ANTIGRAVITY_GUARD_LOST"}
                      if guard_lost else {"native_code": owner.native_code})
            except (B1Error, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                if code != 0:
                    _diag("FRESH ANTIGRAVITY SUPERVISOR CLEANUP: " + (error.code if isinstance(error, B1Error) else type(error).__name__))
        if watch_owner is not None:
            watch_owner.close()
        connection.close()
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--runner", type=int)
    channel = parser.add_mutually_exclusive_group(required=True)
    channel.add_argument("--control-fd", type=int)
    channel.add_argument("--gate-fd", type=int)
    channel.add_argument("--guardian-fd", type=int)
    parser.add_argument("--watch-fd", type=int)
    parser.add_argument("--ready-fd", type=int)
    parser.add_argument("--leader", type=int)
    args = parser.parse_args()
    if args.guardian_fd is not None:
        if args.ready_fd is None or args.leader is None or args.runner is not None:
            parser.error("guardian requires ready fd and leader")
        return guard_native_group(args.plan, args.guardian_fd, args.ready_fd, args.leader)
    if args.gate_fd is not None:
        if args.runner is not None or args.watch_fd is None:
            parser.error("gate requires watch fd and cannot specify runner")
        try:
            return gated_native(args.plan, args.gate_fd, args.watch_fd)
        except (B1Error, OSError, ValueError, KeyError) as error:
            print("FRESH ANTIGRAVITY NATIVE GATE: " + (error.code if isinstance(error, B1Error) else type(error).__name__), file=sys.stderr)
            return 1
    if args.runner is None:
        parser.error("control requires runner")
    with socket.socket(fileno=args.control_fd) as connection:
        connection.set_inheritable(False)
        return supervise(args.plan, args.runner, connection)


if __name__ == "__main__":
    raise SystemExit(main())
