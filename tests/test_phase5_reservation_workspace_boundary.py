#!/usr/bin/env python3
"""Phase 5: reservation paths and roots come from the hook payload workspace."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHECK_HOOK = ROOT / "hooks" / "check-file-reservation.sh"
RELEASE_INVALIDATE_HOOK = ROOT / "hooks" / "invalidate-release-debounce.sh"


class _Server:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = json.loads(body)
                parent.requests.append(request)
                name = request.get("params", {}).get("name")
                structured = {"renewed": 1} if name == "renew_file_reservations" else {"released": 1}
                payload = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "test",
                        "result": {"isError": False, "structuredContent": structured},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/mcp"

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()


def _clean_env(root: Path, ambient: Path, runtime: Path, hooks: Path, url: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in tuple(env):
        if name.startswith("AGENTSTACK_") or name in {
            "PROJECT_KEY",
            "MCP_URL",
            "MCP_AGENT_MAIL_TOKEN",
            "TMUX",
            "TMUX_PANE",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
        }:
            env.pop(name, None)
    env.update(
        {
            "HOME": str(root / "home"),
            "AGENT_NAME": "PluckyEinstein",
            # Deliberately stale ambient values. The payload cwd must win.
            "AGENTSTACK_PROJECT_KEY": str(ambient),
            "AGENTSTACK_PROTECTED_ROOTS": str(ambient),
            "AGENTSTACK_HOOKS_DIR": str(hooks),
            "AGENTSTACK_RUNTIME_DIR": str(runtime),
            "AGENTSTACK_MCP_URL": url,
            "AGENTSTACK_MAIL_HTTP_BEARER_MODE": "disabled",
            "FILE_RESERVATION_RETRY_DELAY_SECONDS": "0",
        }
    )
    return env


def _run_hook(hook: Path, payload: dict[str, Any], env: dict[str, str], process_cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        cwd=process_cwd,
        check=False,
        timeout=30,
    )


class Phase5ReservationWorkspaceBoundaryTests(unittest.TestCase):
    def test_guard_uses_payload_workspace_instead_of_stale_ambient_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            root = Path(directory)
            ambient, actual = root / "ambient", root / "actual"
            runtime, hooks = root / "runtime", root / "hooks"
            for path in (ambient, actual, runtime, hooks):
                path.mkdir(parents=True)
            env = _clean_env(root, ambient, runtime, hooks, server.url)
            target = actual / "note.md"
            result = _run_hook(
                CHECK_HOOK,
                {"session_id": "s1", "cwd": str(actual), "tool_input": {"file_path": str(target)}},
                env,
                ambient,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(server.requests), 1)
        arguments = server.requests[0]["params"]["arguments"]
        self.assertEqual(arguments["project_key"], str(actual.resolve()))
        self.assertEqual(arguments["paths"], ["note.md", str(target.resolve())])

    def test_relative_edit_path_is_resolved_from_payload_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            root = Path(directory)
            ambient, actual = root / "ambient", root / "actual"
            runtime, hooks = root / "runtime", root / "hooks"
            for path in (ambient, actual, runtime, hooks):
                path.mkdir(parents=True)
            env = _clean_env(root, ambient, runtime, hooks, server.url)
            result = _run_hook(
                CHECK_HOOK,
                {"session_id": "s1", "cwd": str(actual), "tool_input": {"file_path": "nested/note.md"}},
                env,
                ambient,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = server.requests[0]["params"]["arguments"]
        expected = actual.resolve() / "nested" / "note.md"
        self.assertEqual(arguments["project_key"], str(actual.resolve()))
        self.assertEqual(arguments["paths"], ["nested/note.md", str(expected)])
        self.assertNotIn(str(ambient.resolve() / "nested" / "note.md"), arguments["paths"])

    def test_linked_worktree_is_protected_by_its_worktree_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            root = Path(directory)
            main, linked = root / "main", root / "linked"
            runtime, hooks = root / "runtime", root / "hooks"
            for path in (main, runtime, hooks):
                path.mkdir(parents=True)
            git_env = os.environ.copy()
            for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
                git_env.pop(name, None)
            subprocess.run(["git", "init", "-q", str(main)], env=git_env, check=True)
            subprocess.run(
                [
                    "git", "-C", str(main), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit",
                    "--allow-empty", "-qm", "fixture",
                ],
                env=git_env,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(main), "worktree", "add", "-qb", "phase5-linked", str(linked)],
                env=git_env,
                check=True,
            )
            env = _clean_env(root, main, runtime, hooks, server.url)
            target = linked / "note.md"
            try:
                result = _run_hook(
                    CHECK_HOOK,
                    {"session_id": "s1", "cwd": str(linked), "tool_input": {"file_path": str(target)}},
                    env,
                    main,
                )
            finally:
                subprocess.run(
                    ["git", "-C", str(main), "worktree", "remove", "--force", str(linked)],
                    env=git_env,
                    check=False,
                )

        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = server.requests[0]["params"]["arguments"]
        self.assertEqual(arguments["project_key"], str(main.resolve()))
        self.assertEqual(arguments["paths"], ["note.md", str(target.resolve())])

    def test_rereservation_invalidation_uses_payload_workspace_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory, _Server() as server:
            root = Path(directory)
            ambient, actual = root / "ambient", root / "actual"
            runtime, hooks = root / "runtime", root / "hooks"
            for path in (ambient, actual, runtime, hooks):
                path.mkdir(parents=True)
            state_dir = runtime / "file_release_debounce"
            state_dir.mkdir()
            key = hashlib.sha1(b"PluckyEinstein\0note.md").hexdigest()
            state_file = state_dir / key
            state_file.write_text("token\n", encoding="utf-8")
            env = _clean_env(root, ambient, runtime, hooks, server.url)
            result = _run_hook(
                RELEASE_INVALIDATE_HOOK,
                {
                    "session_id": "s1",
                    "cwd": str(actual),
                    "tool_input": {
                        "agent_name": "PluckyEinstein",
                        "project_key": str(actual),
                        "paths": [str(actual / "note.md")],
                    },
                },
                env,
                ambient,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(state_file.exists())
        self.assertEqual(server.requests, [])


if __name__ == "__main__":
    unittest.main()
