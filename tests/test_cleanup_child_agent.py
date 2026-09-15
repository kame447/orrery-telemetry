#!/usr/bin/env python3
"""Regression tests for child cleanup identity isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "cleanup-child-agent.sh"


def _arrange_child_state(tmp_path: Path) -> tuple[str, dict[str, str], list[Path]]:
    hooks = tmp_path / "hooks"
    runtime = tmp_path / "runtime"
    state_dir = runtime / "child-agents"
    hooks.mkdir()
    state_dir.mkdir(parents=True)

    agent_name = "Other-Agent"
    (hooks / "resolve-agent-name.sh").write_text(
        f'RESOLVED_AGENT="{agent_name}"\n', encoding="utf-8"
    )
    state_file = state_dir / f"{agent_name}.json"
    state_file.write_text(
        json.dumps(
            {
                "project_key": "/test/project",
                "registration_token": "test-registration-token",
            }
        ),
        encoding="utf-8",
    )
    token_file = runtime / f"agent_token_{agent_name}"
    token_file.write_text("test-registration-token", encoding="utf-8")
    mcp_config = state_dir / f"{agent_name}.mcp.json"
    mcp_config.write_text("{}", encoding="utf-8")
    codex_home = state_dir / f"{agent_name}.codex-home"
    codex_home.mkdir()
    managed_file = runtime / "managed_agents.txt"
    managed_file.write_text(f"{agent_name}\n", encoding="utf-8")

    env = os.environ.copy()
    for inherited_name in (
        "AGENT_NAME",
        "PROJECT_KEY",
        "AGENTSTACK_PROJECT_KEY",
        "CHILD_REGISTRATION_TOKEN",
        "MCP_AGENT_MAIL_TOKEN",
        "AGENTSTACK_MAIL_ENV",
        "MCP_URL",
    ):
        env.pop(inherited_name, None)
    env.update(
        {
            "AGENTSTACK_HOOKS_DIR": str(hooks),
            "AGENTSTACK_RUNTIME_DIR": str(runtime),
            "AGENTSTACK_MANAGED_AGENTS_FILE": str(managed_file),
            "AGENTSTACK_MAIL_HTTP_BEARER_MODE": "disabled",
            "AGENTSTACK_MCP_URL": "http://127.0.0.1:1/mcp",
        }
    )
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    env["AGENTSTACK_REGISTER_LIB"] = str(ROOT / "bin/lib/agentstack-register.sh")
    state_file.chmod(0o600)
    token_file.chmod(0o600)
    published = subprocess.run(
        ["/bin/bash", "-c",
         '. "$1"; ctx=$(agentstack_resolve_invocation_context "$2" /test/project) || exit; '
         'ags_store_registration_token "$3" test-registration-token "$ctx" preregister-child',
         "fixture", env["AGENTSTACK_REGISTER_LIB"], str(ROOT), agent_name],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert published.returncode == 0, published.stderr
    owner_file = runtime / f"agent_owner_{agent_name}.json"
    artifacts = [state_file, token_file, mcp_config, codex_home, owner_file]
    return agent_name, env, artifacts


def test_cleanup_without_explicit_identity_does_not_retire_resolved_agent(
    tmp_path: Path,
) -> None:
    """Ambient resolver state must never select another agent for cleanup."""
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(HOOK)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert all(path.exists() for path in artifacts)
    managed_file = Path(env["AGENTSTACK_MANAGED_AGENTS_FILE"])
    assert managed_file.read_text(encoding="utf-8") == f"{agent_name}\n"


def test_cleanup_with_argument_still_cleans_named_child(tmp_path: Path) -> None:
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(HOOK), agent_name],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not any(path.exists() for path in artifacts)


def test_cleanup_with_entry_environment_still_cleans_child(tmp_path: Path) -> None:
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    env["AGENT_NAME"] = agent_name
    result = subprocess.run(
        ["/bin/bash", str(HOOK)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not any(path.exists() for path in artifacts)


def test_invalid_child_state_fails_loudly_without_partial_cleanup(
    tmp_path: Path,
) -> None:
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    artifacts[0].write_text("{not-json", encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(HOOK), agent_name],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "could not read child state" in result.stderr
    assert all(path.exists() for path in artifacts)


def test_foreign_workspace_cannot_clean_an_owned_child(tmp_path: Path) -> None:
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    # The marker and an exact-looking key still cannot prove this workspace.
    env.update(AGENTSTACK_PROJECT_KEY="/test/project", AGENTSTACK_PROJECT_CONTEXT="1")
    result = subprocess.run(["/bin/bash", str(HOOK), agent_name], cwd=foreign,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert all(path.exists() for path in artifacts)


def test_replacement_token_is_not_deleted_by_an_old_process(tmp_path: Path) -> None:
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    env["CHILD_REGISTRATION_TOKEN"] = "token-from-a-previous-process"
    result = subprocess.run(["/bin/bash", str(HOOK), agent_name], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert all(path.exists() for path in artifacts)


def test_stale_namespace_does_not_change_cleanup_requests(tmp_path: Path) -> None:
    from test_check_file_reservation import _Server
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    env.update(AGENTSTACK_PROJECT_KEY="wrong-team", PROJECT_KEY="wrong-team")
    answer = json.dumps({"result": {"structuredContent": {"ok": True}}}).encode()
    with _Server(lambda _: (200, answer)) as server:
        env["AGENTSTACK_MCP_URL"] = server.url
        result = subprocess.run(["/bin/bash", str(HOOK), agent_name], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not any(path.exists() for path in artifacts)
    calls = [item["json"]["params"] for item in server.requests]
    assert [item["name"] for item in calls] == ["release_file_reservations", "retire_agent"]
    assert all(item["arguments"]["project_key"] == "/test/project" for item in calls)
    assert calls[-1]["arguments"]["registration_token"] == "test-registration-token"
    assert "test-registration-token" not in result.stdout + result.stderr


def test_owner_replacement_during_mail_prevents_local_deletion(tmp_path: Path) -> None:
    from test_check_file_reservation import _Server
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    owner_file = artifacts[-1]
    def answer(_count):
        data = json.loads(owner_file.read_text())
        data["token_sha256"] = "replacement-generation"
        owner_file.write_text(json.dumps(data))
        return 200, b'{"result":{"structuredContent":{"ok":true}}}'
    with _Server(answer) as server:
        env["AGENTSTACK_MCP_URL"] = server.url
        result = subprocess.run(["/bin/bash", str(HOOK), agent_name], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert all(path.exists() for path in artifacts)
    assert [item["json"]["params"]["name"] for item in server.requests] == ["release_file_reservations"]


def test_symlinked_owner_is_not_cleanup_authority(tmp_path: Path) -> None:
    agent_name, env, artifacts = _arrange_child_state(tmp_path)
    owner = artifacts[-1]
    saved = tmp_path / "saved-owner"
    owner.rename(saved)
    owner.symlink_to(saved)
    result = subprocess.run(["/bin/bash", str(HOOK), agent_name], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert all(path.exists() for path in artifacts)


def _main() -> int:
    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                function(Path(tmp))
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())
