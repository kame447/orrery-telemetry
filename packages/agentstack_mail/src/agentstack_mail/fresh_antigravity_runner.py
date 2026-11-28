"""Own one native agy stream and deliver authenticated fresh Mail turns."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import runpy
import select
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time

from .fresh_antigravity import read_plan, validate_resume_history
from .fresh_antigravity_supervisor import SupervisedNative
from .fresh_launch import identity, native_id, record_session
from .fresh_notifications import IMPORTANCE, consumer_lock
from .fresh_runtime import _directory, _read_private, _write_private
from .storage_origin_contract import B1Error


MAX_EVENT_BYTES = 16 * 1024 * 1024
INITIALIZATION_TIMEOUT = 120
BOOTSTRAP_PROMPT = (
    "Initialize this conversation only. Do not use tools or read, create, or edit files. "
    "Reply with READY. Further task instructions may follow in later turns."
)


class Interrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


class Runner:
    def __init__(self, plan_path):
        self.plan_path = Path(plan_path)
        self.client, self.plan, self.run, self.intent = read_plan(self.plan_path)
        self.state = self.run / "antigravity"
        _directory(self.state)
        self.expected = identity(self.client)
        self.process = None
        self.listener = None
        self.socket_directory = self.socket_path = self.socket_identity = None
        self.session_id = None
        self.initialized = False
        self.bootstrap = True
        self.busy = False
        self.completed_turns = 0
        self.owns_run = False
        self.resume_history_admitted = False
        self.pending_wake = False
        self.pending_input = None
        self.exit_requested = False
        self.selector = None
        self.terminal = self.terminal_registered = False
        self.terminal_buffer = bytearray()
        self.native_eof = False
        self.native_code = None

    def check(self):
        client, plan, run, intent = read_plan(self.plan_path)
        if (client.context_digest != self.client.context_digest or plan != self.plan
                or run != self.run or intent != self.intent or identity(client) != self.expected):
            raise B1Error("FRESH_LAUNCH_RECEIPT_MISMATCH")

    def check_supervisor(self):
        # The only control messages after startup are termination receipts.
        # Readability (including EOF) before requesting stop is unexpected.
        if select.select([self.process.control], [], [], 0)[0]:
            raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_LOST")

    def send(self, text):
        self.check()
        self.check_supervisor()
        if not isinstance(text, str) or not text.strip():
            raise B1Error("FRESH_ANTIGRAVITY_EMPTY_TURN")
        data = json.dumps({"event": "user", "message": {"content": text}}, ensure_ascii=False)
        self.process.stdin.write((data + "\n").encode())
        self.process.stdin.flush()
        self.busy = True

    def observe_session(self):
        if self.session_id is None or self.initialized:
            return
        self.check()
        hook_path = self.state / "hook-session.json"
        if not hook_path.exists() and not hook_path.is_symlink():
            return
        hook = _read_private(hook_path)
        expected = dict(native_session_id=self.session_id, launch_id=self.run.name,
                        context_digest=self.client.context_digest, **self.expected)
        if hook != expected:
            raise B1Error("FRESH_NATIVE_SESSION_MISMATCH")
        record = dict(expected, harness="antigravity", native_pid=self.process.pid,
                      runner_pid=os.getpid())
        _write_private(self.state / "native-session.json", record)
        asyncio.run(record_session(self.client, {
            "hook_event_name": "SessionStart", "session_id": self.session_id}))
        self.initialized = True
        print(f"[antigravity] fresh session {self.session_id}", flush=True)

    def event(self, event):
        if not isinstance(event, dict):
            return
        kind = event.get("event")
        if kind == "init":
            session_id = native_id(event.get("conversation_id"))
            if (self.session_id is not None and self.session_id != session_id
                    or self.plan.get("resume_session_id") not in (None, session_id)):
                raise B1Error("FRESH_NATIVE_SESSION_MISMATCH")
            self.session_id = session_id
            self.observe_session()
        elif kind == "step_update":
            update = event.get("step_update")
            delta = update.get("text_delta") if isinstance(update, dict) else None
            if isinstance(delta, str) and delta:
                print(delta, end="", flush=True)
        elif kind == "result":
            result = event.get("result")
            if not self.busy or not isinstance(result, dict):
                raise B1Error("FRESH_ANTIGRAVITY_RESULT_INVALID")
            status, response = result.get("status"), result.get("response")
            denied = result.get("denied_actions", [])
            if (not isinstance(status, str) or status.upper() != "SUCCESS"
                    or not isinstance(response, str) or not response.strip()
                    or not isinstance(denied, list) or denied):
                raise B1Error("FRESH_ANTIGRAVITY_TURN_INCOMPLETE")
            self.observe_session()
            if not self.initialized:
                raise B1Error("FRESH_ANTIGRAVITY_HOOK_UNCONFIRMED")
            self.busy = False
            if self.bootstrap:
                self.bootstrap = False
                prompt = self.plan["prompt"]
                if (isinstance(prompt, str) and prompt.strip() and not self.exit_requested
                        and not self.plan.get("resume_session_id")):
                    self.send(prompt)
                elif self.plan.get("resume_session_id"):
                    print("[antigravity] resumed; waiting for Mail", flush=True)
            else:
                self.completed_turns += 1
                print(f"\n[antigravity] native turn {self.completed_turns}: SUCCESS; waiting for Mail", flush=True)
            self.drain_idle()

    def drain_idle(self):
        if not self.initialized or self.bootstrap or self.busy or self.exit_requested:
            return
        if self.pending_wake:
            self.pending_wake = False
            asyncio.run(self.notifications())
        if not self.busy and self.pending_input is not None:
            text, self.pending_input = self.pending_input, None
            self.send(text)
        self.update_terminal()

    def update_terminal(self):
        if not self.terminal:
            return
        available = self.pending_input is None and not self.exit_requested
        if available and not self.terminal_registered:
            self.selector.register(sys.stdin, selectors.EVENT_READ, "terminal")
            self.terminal_registered = True
        elif not available and self.terminal_registered:
            self.selector.unregister(sys.stdin)
            self.terminal_registered = False

    def terminal_input(self):
        # Stop reading when the one queued line is full; later input remains in
        # the terminal instead of being consumed and silently discarded.
        byte = os.read(sys.stdin.fileno(), 1)
        if not byte:
            self.exit_requested = True
            self.update_terminal()
            return
        if byte != b"\n":
            self.terminal_buffer.extend(byte)
            if len(self.terminal_buffer) > 65536:
                raise B1Error("FRESH_CHILD_TASK_INVALID")
            return
        text = self.terminal_buffer.decode("utf-8").rstrip("\r")
        self.terminal_buffer.clear()
        if text.strip() == "/exit":
            self.exit_requested = True
            print("[antigravity] exiting after the current turn; Ctrl-C interrupts", flush=True)
        elif text.strip():
            if self.busy or not self.initialized or self.bootstrap:
                self.pending_input = text
                print("[antigravity] input queued for the next turn", flush=True)
            else:
                self.send(text)
        self.update_terminal()

    def bind(self):
        temporary_root = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        self.socket_directory = Path(tempfile.mkdtemp(prefix="agy-fresh-", dir=temporary_root))
        _directory(self.socket_directory)
        self.socket_path = self.socket_directory / "wake.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        info = self.socket_path.lstat()
        self.socket_identity = (info.st_dev, info.st_ino)
        self.listener.listen(8)
        self.listener.setblocking(False)
        _write_private(self.state / "socket.json", dict(path=str(self.socket_path),
            device=info.st_dev, inode=info.st_ino, runner_pid=os.getpid(),
            launch_id=self.run.name, context_digest=self.client.context_digest, **self.expected))

    async def notifications(self):
        self.check()
        helpers = runpy.run_path(str(Path(self.plan["wrapper_root"]) / "bin/lib/runtime_delivery.py"))
        minimum = os.environ.get("AGENTSTACK_MAIL_NOTIFY_MIN_IMPORTANCE", "low").lower()
        if minimum not in IMPORTANCE:
            minimum = "normal"
        with consumer_lock(self.client) as acquired:
            if not acquired or os.environ.get("AGENTSTACK_MAIL_DISABLED") == "1":
                return False
            batch = await self.client.call("notification_pending", {"min_importance": minimum})
            if any(batch.get(k) != v for k, v in self.expected.items()):
                raise B1Error("FRESH_PROXY_BINDING_MISMATCH")
            notices, selected = [], []
            for message in batch["messages"]:
                if not helpers["notification_eligible"](message):
                    continue
                latest = await self.client.call("notification_pending", {
                    "message_id": message["id"], "limit": 1, "min_importance": minimum})
                if any(latest.get(k) != v for k, v in self.expected.items()):
                    raise B1Error("FRESH_PROXY_BINDING_MISMATCH")
                if latest["messages"]:
                    notices.append(helpers["format_notification"](latest["messages"][0]))
                    selected.append(message["id"])
            if not notices:
                return False
            self.send("\n".join(notices))
            for message_id in selected:
                self.check()
                await self.client.call("notification_complete", {"message_id": message_id})
            return True

    def wake(self):
        connection, _ = self.listener.accept()
        with connection:
            connection.settimeout(1)
            raw = bytearray()
            try:
                while b"\n" not in raw and len(raw) <= 4096:
                    part = connection.recv(4097 - len(raw))
                    if not part:
                        break
                    raw.extend(part)
                request = json.loads(raw)
            except (OSError, ValueError):
                request = None
            expected = {"launch_id": self.run.name, "nonce": self.intent["nonce"]}
            accepted = False
            if request == expected and len(raw) <= 4096 and not self.exit_requested:
                self.check()
                if self.initialized and not self.bootstrap and not self.busy:
                    self.pending_wake = False
                    accepted = asyncio.run(self.notifications())
                else:
                    self.pending_wake = True
            try:
                connection.sendall(json.dumps({"accepted": accepted}).encode() + b"\n")
            except OSError:
                pass  # An uncertain sender may retry; Mail facts remain authoritative.

    def execute(self):
        self.check()
        log_fd = os.open(self.state / "events.jsonl",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.owns_run = True
        with os.fdopen(log_fd, "wb") as log, selectors.DefaultSelector() as selector:
            self.selector = selector
            validate_resume_history(self.client, self.run, self.plan)
            self.resume_history_admitted = True
            self.bind()
            self.process = SupervisedNative()
            self.process.start(self.plan_path)
            selector.register(self.listener, selectors.EVENT_READ, "wake")
            selector.register(self.process.stdout, selectors.EVENT_READ, "native")
            selector.register(self.process.control, selectors.EVENT_READ, "supervisor")
            self.terminal = sys.stdin.isatty()
            if self.terminal:
                print("[antigravity] ORRERY stream session: type a line to send a turn; /exit finishes, Ctrl-C interrupts", flush=True)
                self.update_terminal()
            self.send(BOOTSTRAP_PROMPT)
            started, buffer = time.monotonic(), bytearray()
            while True:
                self.check_supervisor()
                if self.exit_requested and not self.busy:
                    return 0
                if self.bootstrap and time.monotonic() - started > INITIALIZATION_TIMEOUT:
                    raise B1Error("FRESH_ANTIGRAVITY_STARTUP_TIMEOUT")
                events = selector.select(0.5)
                if any(key.data == "supervisor" for key, _ in events):
                    raise B1Error("FRESH_ANTIGRAVITY_SUPERVISOR_LOST")
                for key, _ in events:
                    if key.data == "wake":
                        self.wake()
                        continue
                    if key.data == "terminal":
                        self.terminal_input()
                        continue
                    data = os.read(self.process.stdout.fileno(), 65536)
                    if not data:
                        if buffer:
                            raise B1Error("FRESH_ANTIGRAVITY_STREAM_TRUNCATED")
                        self.native_eof = True
                        return 0 if self.initialized and not self.busy else 1
                    log.write(data)
                    log.flush()
                    buffer.extend(data)
                    if len(buffer) > MAX_EVENT_BYTES:
                        raise B1Error("FRESH_ANTIGRAVITY_EVENT_TOO_LARGE")
                    while b"\n" in buffer:
                        raw, _, rest = buffer.partition(b"\n")
                        buffer[:] = rest
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        self.event(event)

    def stop_native(self):
        if self.process is None:
            if self.owns_run:
                _write_private(self.state / "native-not-started.json", dict(
                    launch_id=self.run.name, context_digest=self.client.context_digest, **self.expected))
            return
        self.native_code = self.process.stop()

    def close(self):
        cleanup_error = None
        try:
            self.stop_native()
        except (B1Error, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            cleanup_error = error
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        if self.listener is not None:
            self.listener.close()
        if self.socket_path is not None:
            try:
                info = self.socket_path.lstat()
                if (info.st_dev, info.st_ino) == self.socket_identity:
                    self.socket_path.unlink()
                else:
                    raise B1Error("FRESH_ANTIGRAVITY_SOCKET_CHANGED")
                self.socket_directory.rmdir()
            except FileNotFoundError:
                pass
            except (B1Error, OSError) as error:
                cleanup_error = cleanup_error or error
        if self.owns_run and cleanup_error is None:
            from .fresh_antigravity_end import release_ready, verify_native_ended
            observation = verify_native_ended(self.client, Path(self.plan["wrapper_root"]),
                                              self.run, runner_pid=os.getpid())
            _write_private(self.state / "native-ended.json", dict(
                launch_id=self.run.name, context_digest=self.client.context_digest,
                **self.expected, observation=observation))
            from .fresh_child import launch_task
            task, _ = launch_task(self.client)
            if (task.get("spawn_id") if task is not None else None) != self.intent.get("child_spawn_id"):
                raise B1Error("FRESH_CHILD_BINDING_MISMATCH")
            resources = task.get("resources", []) if task is not None else []
            # Admission alone is not a native start. A refused or non-started
            # resume must keep the earlier session's reservations intact.
            resumed_without_native = (self.plan.get("resume_session_id") is not None
                                      and observation.get("state") == "not-started")
            if resources and self.resume_history_admitted and not resumed_without_native:
                _write_private(self.state / "release-ready.json",
                               release_ready(self.client, self.run, self.intent, task, observation))
        if cleanup_error is not None:
            raise cleanup_error


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    runner = None

    def interrupted(signum, frame):
        raise Interrupted(signum)

    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
    code = 1
    try:
        runner = Runner(args.plan)
        code = runner.execute()
    except Interrupted as error:
        code = 128 + error.signum
    except (B1Error, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("FRESH ANTIGRAVITY: " + (error.code if isinstance(error, B1Error)
              else "FRESH_ANTIGRAVITY_RUN_FAILED"), file=sys.stderr)
    finally:
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        if runner is not None:
            try:
                runner.close()
                if code == 0 and runner.native_eof and runner.native_code:
                    code = runner.native_code if runner.native_code > 0 else 128 - runner.native_code
            except (B1Error, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
                cause = error.code if isinstance(error, B1Error) else None
                if not isinstance(cause, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", cause) is None:
                    cause = type(error).__name__
                if isinstance(error, OSError) and type(error.errno) is int:
                    cause += " errno=" + str(error.errno)
                print("FRESH ANTIGRAVITY: FRESH_ANTIGRAVITY_CLEANUP_INCOMPLETE (" + cause + ")",
                      file=sys.stderr)
                code = code or 1
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
