"""Audit regression: stale AGENT_NAME must not outrank the hook session binding."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

from test_check_file_reservation import _Server, _mcp_result

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "check-file-reservation.sh"


def _write_owner(runtime: Path, repo: Path, name: str, project: str, token: str) -> None:
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "_.-") else "_" for ch in name)
    owner = {
        "schema": 1,
        "agent_name": name,
        "name_key": name.replace("-", "").casefold(),
        "project_key": project,
        "repository_key": str(repo),
        "non_git_root": None,
        "created_by": "audit",
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "updated_at": "2026-09-15T00:00:00Z",
    }
    owner_path = runtime / f"agent_owner_{safe}.json"
    token_path = runtime / f"agent_token_{safe}"
    owner_path.write_text(json.dumps(owner) + "\n", encoding="utf-8")
    token_path.write_text(token, encoding="utf-8")
    owner_path.chmod(0o600)
    token_path.chmod(0o600)


def test_stale_env_identity_cannot_borrow_another_namespace_for_reservation(tmp_path: Path) -> None:
    repo = (tmp_path / "repo").resolve()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    runtime = tmp_path / "runtime"
    index = runtime / "session_index"
    index.mkdir(parents=True)

    stale_name, stale_project = "StaleCurie", "mail-project-stale"
    real_name, real_project = "RealFaraday", "mail-project-real"
    _write_owner(runtime, repo, stale_name, stale_project, "stale-token")
    _write_owner(runtime, repo, real_name, real_project, "real-token")

    # The hook payload identifies the real session. Its durable schema-3 binding
    # and owner both agree with the actual repository and real custom namespace.
    (index / "real.json").write_text(
        json.dumps({
            "schema_version": 3,
            "binding_kind": "self",
            "registered_by": real_name,
            "session_id": "session-real",
            "agent_name": real_name,
            "project_key": real_project,
            "repository_key": str(repo),
            "work_dir": str(repo),
        }) + "\n",
        encoding="utf-8",
    )

    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENTSTACK_", "GIT_"))
        and key not in {"PROJECT_KEY", "AGENT_NAME", "TMUX", "TMUX_PANE", "MCP_URL"}
    }
    env.update({
        "HOME": str(tmp_path / "home"),
        "AGENTSTACK_HOOKS_DIR": str(ROOT / "hooks"),
        "AGENTSTACK_RUNTIME_DIR": str(runtime),
        "AGENTSTACK_MAIL_HTTP_BEARER_MODE": "disabled",
        "AGENT_NAME": stale_name,  # stale process environment
        "AGENTSTACK_PROJECT_KEY": "ambient-wrong-project",
        "FILE_RESERVATION_RETRY_DELAY_SECONDS": "0",
    })
    Path(env["HOME"]).mkdir()
    payload = {
        "session_id": "session-real",
        "cwd": str(repo),
        "tool_input": {"file_path": str(repo / "note.md")},
    }

    with _Server(lambda _: (200, _mcp_result(0))) as server:
        env["AGENTSTACK_MCP_URL"] = server.url
        result = subprocess.run(
            ["/bin/bash", str(HOOK)],
            input=json.dumps(payload), cwd=repo, env=env,
            text=True, capture_output=True, timeout=20,
        )

    # Safe outcomes are either fail-closed before Mail or using the session's
    # real owner. The stale env identity/project is never an acceptable actor.
    if not server.requests:
        assert result.returncode == 2, result.stderr
        return
    arguments = [request["json"]["params"]["arguments"] for request in server.requests]
    assert all(item.get("project_key") == real_project for item in arguments), (
        result.stderr, arguments
    )
    assert all(item.get("agent_name") == real_name for item in arguments), (
        result.stderr, arguments
    )
