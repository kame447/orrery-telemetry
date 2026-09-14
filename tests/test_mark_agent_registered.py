from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "mark-agent-registered.sh"


def _invoke_hook(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    session_id = f"identity-contract-{uuid.uuid4().hex}"
    flag = Path(f"/tmp/.claude-agent-registered-{session_id}")
    flag.unlink(missing_ok=True)
    workspace = _workspace(tmp_path)
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        payload = {**payload, "tool_input": {**tool_input, "project_key": str(workspace)}}
    payload = {"session_id": session_id, "cwd": str(workspace), **payload}
    runtime_dir = tmp_path / "runtime"
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    # Phase4 validates the registered project against the hook's actual cwd,
    # so the real context resolver and register library must be reachable.
    # record-session-index.py stays absent: these tests cover the flag contract.
    shutil.copy2(ROOT / "hooks" / "project-context.sh", hooks_dir / "project-context.sh")
    env = os.environ.copy()
    env.update(
        {
            "AGENTSTACK_HOOKS_DIR": str(hooks_dir),
            "AGENTSTACK_RUNTIME_DIR": str(runtime_dir),
            "AGENTSTACK_REGISTER_LIB": str(ROOT / "bin" / "lib" / "agentstack-register.sh"),
        }
    )
    completed = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return completed, flag, runtime_dir


def _workspace(tmp_path: Path) -> Path:
    """A real repository whose resolved project key is the registered project."""
    workspace = (tmp_path / "workspace").resolve()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    subprocess.run(["git", "init", "-q", str(workspace)], env=env, check=True,
                   capture_output=True, timeout=30)
    return workspace.resolve()


def _run_hook(
    tmp_path: Path,
    *,
    requested_name: str | None,
    returned_name: str | None,
    include_tool_input: bool = True,
    response_is_error: bool = False,
    response_has_embedded_error: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    tool_input: dict[str, str] = {
        # Replaced by the actual workspace key in _invoke_hook.
        "project_key": "",
        "program": "claude-code",
        "model": "fixture-model",
    }
    if requested_name is not None:
        tool_input["name"] = requested_name
    tool_response: dict[str, object] = {"id": 7}
    if returned_name is not None:
        tool_response["name"] = returned_name
    response_payload: dict[str, object] = tool_response
    if response_is_error:
        response_payload = {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(tool_response),
                }
            ],
        }
    elif response_has_embedded_error:
        response_payload = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "error": "registration failed",
                            "name": returned_name,
                        }
                    ),
                }
            ],
        }
    payload: dict[str, object] = {
        "tool_response": json.dumps(response_payload),
    }
    if include_tool_input:
        payload["tool_input"] = tool_input
    return _invoke_hook(tmp_path, payload)


def test_explicit_matching_name_marks_registration_success(tmp_path: Path) -> None:
    completed, flag, _ = _run_hook(
        tmp_path,
        requested_name="ProOpus",
        returned_name="ProOpus",
    )
    try:
        assert completed.returncode == 0, completed.stderr
        assert flag.is_file()
    finally:
        flag.unlink(missing_ok=True)


def test_omitted_name_adopts_server_generated_identity(tmp_path: Path) -> None:
    completed, flag, _ = _run_hook(
        tmp_path,
        requested_name=None,
        returned_name="GreenCastle",
    )
    try:
        assert completed.returncode == 0, completed.stderr
        assert flag.is_file()
    finally:
        flag.unlink(missing_ok=True)


def test_matching_structured_and_text_projections_mark_success(tmp_path: Path) -> None:
    completed, flag, _ = _invoke_hook(
        tmp_path,
        {
            "tool_input": {"name": "ProOpus"},
            "tool_response": {
                "isError": False,
                "structuredContent": {"name": "ProOpus"},
                "content": [
                    {"type": "text", "text": json.dumps({"name": "ProOpus"})}
                ],
            },
        },
    )
    try:
        assert completed.returncode == 0, completed.stderr
        assert flag.is_file()
    finally:
        flag.unlink(missing_ok=True)


def test_aliased_cwd_and_project_of_the_same_workspace_mark_success(tmp_path: Path) -> None:
    """A symlinked cwd (macOS /tmp, a user link) is still the same workspace."""
    workspace = _workspace(tmp_path)
    alias = tmp_path / "alias-to-workspace"
    alias.symlink_to(workspace, target_is_directory=True)
    session_id = f"identity-contract-{uuid.uuid4().hex}"
    flag = Path(f"/tmp/.claude-agent-registered-{session_id}")
    hooks_dir = tmp_path / "alias-hooks"
    hooks_dir.mkdir()
    shutil.copy2(ROOT / "hooks" / "project-context.sh", hooks_dir / "project-context.sh")
    env = os.environ.copy()
    env.update({
        "AGENTSTACK_HOOKS_DIR": str(hooks_dir),
        "AGENTSTACK_RUNTIME_DIR": str(tmp_path / "runtime"),
        "AGENTSTACK_REGISTER_LIB": str(ROOT / "bin" / "lib" / "agentstack-register.sh"),
    })
    payload = {
        "session_id": session_id,
        "cwd": str(alias),
        "tool_input": {"name": "ProOpus", "project_key": str(alias)},
        "tool_response": json.dumps({"id": 7, "name": "ProOpus"}),
    }
    try:
        completed = subprocess.run(["/bin/bash", str(HOOK)], input=json.dumps(payload),
                                   text=True, capture_output=True, env=env, check=False)
        assert completed.returncode == 0, completed.stderr
        assert flag.is_file()
    finally:
        flag.unlink(missing_ok=True)


def test_project_of_another_workspace_is_not_marked(tmp_path: Path) -> None:
    """The null case for the alias test: a real mismatch is still refused."""
    other = tmp_path / "other"
    other.mkdir()
    session_id = f"identity-contract-{uuid.uuid4().hex}"
    flag = Path(f"/tmp/.claude-agent-registered-{session_id}")
    hooks_dir = tmp_path / "mismatch-hooks"
    hooks_dir.mkdir()
    shutil.copy2(ROOT / "hooks" / "project-context.sh", hooks_dir / "project-context.sh")
    env = os.environ.copy()
    env.update({
        "AGENTSTACK_HOOKS_DIR": str(hooks_dir),
        "AGENTSTACK_RUNTIME_DIR": str(tmp_path / "runtime"),
        "AGENTSTACK_REGISTER_LIB": str(ROOT / "bin" / "lib" / "agentstack-register.sh"),
    })
    payload = {
        "session_id": session_id,
        "cwd": str(_workspace(tmp_path)),
        "tool_input": {"name": "ProOpus", "project_key": str(other.resolve())},
        "tool_response": json.dumps({"id": 7, "name": "ProOpus"}),
    }
    try:
        completed = subprocess.run(["/bin/bash", str(HOOK)], input=json.dumps(payload),
                                   text=True, capture_output=True, env=env, check=False)
        assert completed.returncode != 0
        assert not flag.exists()
        assert "project namespace is not owned" in completed.stderr
    finally:
        flag.unlink(missing_ok=True)


def test_explicit_name_substitution_fails_before_marking_success(
    tmp_path: Path,
) -> None:
    completed, flag, runtime_dir = _run_hook(
        tmp_path,
        requested_name="ProOpus",
        returned_name="DarkKoch",
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "ProOpus" in completed.stderr
        assert "DarkKoch" in completed.stderr
        failure_log = runtime_dir / "registration-failures.log"
        assert failure_log.is_file()
        log = failure_log.read_text(encoding="utf-8")
        assert "name_mismatch" in log
        assert "ProOpus" in log
        assert "DarkKoch" in log
    finally:
        flag.unlink(missing_ok=True)


def test_response_without_name_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _run_hook(
        tmp_path,
        requested_name="ProOpus",
        returned_name=None,
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "malformed_response_channel" in completed.stderr
        assert "malformed_response_channel" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_missing_tool_input_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _run_hook(
        tmp_path,
        requested_name=None,
        returned_name="DarkKoch",
        include_tool_input=False,
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "missing_tool_input" in completed.stderr
        assert "missing_tool_input" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_error_response_with_embedded_name_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _run_hook(
        tmp_path,
        requested_name="ProOpus",
        returned_name="ProOpus",
        response_is_error=True,
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "error_response" in completed.stderr
        assert "error_response" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_embedded_error_object_with_name_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _run_hook(
        tmp_path,
        requested_name="ProOpus",
        returned_name="ProOpus",
        response_has_embedded_error=True,
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "error_response" in completed.stderr
        assert "error_response" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_non_boolean_error_flag_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _invoke_hook(
        tmp_path,
        {
            "tool_input": {"name": "ProOpus"},
            "tool_response": {"isError": "true", "name": "ProOpus"},
        },
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "error_response" in completed.stderr
        assert "error_response" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_tool_response_and_result_name_conflict_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _invoke_hook(
        tmp_path,
        {
            "tool_input": {"name": "ProOpus"},
            "tool_response": {"name": "ProOpus"},
            "tool_result": {"name": "DarkKoch"},
        },
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "inconsistent_response_names" in completed.stderr
        assert "inconsistent_response_names" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_outer_and_nested_name_conflict_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _invoke_hook(
        tmp_path,
        {
            "tool_input": {"name": "ProOpus"},
            "tool_response": {
                "name": "ProOpus",
                "content": [
                    {"type": "text", "text": json.dumps({"name": "DarkKoch"})}
                ],
            },
        },
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "inconsistent_response_names" in completed.stderr
        assert "inconsistent_response_names" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


def test_multiple_content_name_conflict_fails_closed(tmp_path: Path) -> None:
    completed, flag, runtime_dir = _invoke_hook(
        tmp_path,
        {
            "tool_input": {"name": "ProOpus"},
            "tool_response": {
                "content": [
                    {"type": "text", "text": json.dumps({"name": "ProOpus"})},
                    {"type": "text", "text": json.dumps({"name": "DarkKoch"})},
                ],
            },
        },
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "inconsistent_response_names" in completed.stderr
        assert "inconsistent_response_names" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "invalid_response",
    ("not-json", {}),
    ids=("unparseable", "empty-object"),
)
def test_invalid_response_channel_cannot_hide_behind_valid_result(
    tmp_path: Path,
    invalid_response: object,
) -> None:
    completed, flag, runtime_dir = _invoke_hook(
        tmp_path,
        {
            "tool_input": {"name": "ProOpus"},
            "tool_response": invalid_response,
            "tool_result": {"name": "ProOpus"},
        },
    )
    try:
        assert completed.returncode != 0
        assert not flag.exists()
        assert "malformed_response_channel" in completed.stderr
        assert "malformed_response_channel" in (
            runtime_dir / "registration-failures.log"
        ).read_text(encoding="utf-8")
    finally:
        flag.unlink(missing_ok=True)
