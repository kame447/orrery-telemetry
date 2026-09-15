from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "bin/lib/agentstack-register.sh"
SKILL = ROOT / "skills/delegate/SKILL.md"


def _env(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGENTSTACK_", "GIT_")) and k not in
           {"HOME", "PROJECT_KEY", "AGENT_NAME", "PARENT_AGENT",
            "CHILD_REGISTRATION_TOKEN", "MCP_AGENT_MAIL_TOKEN", "BASH_ENV", "ENV"}}
    home = tmp_path / "home"
    home.mkdir()
    env.update(HOME=str(home), AGENTSTACK_RUNTIME_DIR=str(tmp_path / "runtime"),
               AGENTSTACK_MAIL_HTTP_BEARER_MODE="disabled")
    return env


def _run(env: dict[str, str], cwd: Path, body: str, *args: str):
    script = (
        'set -euo pipefail; . "$1"; shift; '
        'MAIL_CALLS=${MAIL_CALLS:-}; '
        'ags_verify_registration_owner_with_mail(){ '
        'printf "%s|%s\\n" "$1" "$2" >> "$MAIL_CALLS"; return "${MAIL_VERIFY_RC:-0}"; }; '
        + body
    )
    return subprocess.run(["/bin/bash", "-c", script, "fixture", str(LIB), *args],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=25)


def _publish(env: dict[str, str], cwd: Path, name: str, token: str, project: str = "team-x"):
    result = _run(env, cwd,
        'ctx=$(agentstack_resolve_invocation_context "$PWD" "$1"); '
        'ags_store_registration_token "$2" "$3" "$ctx" preregister-child',
        project, name, token)
    assert result.returncode == 0, result.stderr


def test_strong_child_owner_authorizes_same_workspace_without_parent(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ChildCurie", "child-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o600)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" "" team-x; '
        'printf "%s\\n" "$AGENTSTACK_PROJECT_KEY"; printf "%s\\n" "$AGS_PREREGISTERED_OWNER_PREEXISTED"',
        str(one_shot))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["team-x", "1"]
    assert not Path(env["MAIL_CALLS"]).exists()


def test_contradictory_child_owner_never_downgrades_to_parent(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ParentCurie", "parent-token")
    _publish(env, work, "ChildCurie", "original-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("replacement-token"); one_shot.chmod(0o600)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x', str(one_shot))
    assert result.returncode != 0
    assert not Path(env["MAIL_CALLS"]).exists()


def test_ownerless_child_requires_parent_owner_and_mail_proof(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ParentCurie", "parent-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o600)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x; '
        'printf "%s\\n" "$AGENTSTACK_PROJECT_KEY"; printf "%s\\n" "$AGS_PREREGISTERED_OWNER_PREEXISTED"',
        str(one_shot))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["team-x", "0"]
    assert Path(env["MAIL_CALLS"]).read_text().strip() == "team-x|ChildCurie"


def test_ownerless_foreign_workspace_fails_before_child_mail_proof(tmp_path: Path):
    owned = tmp_path / "owned"; owned.mkdir()
    foreign = tmp_path / "foreign"; foreign.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, owned, "ParentCurie", "parent-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o600)
    result = _run(env, foreign,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x', str(one_shot))
    assert result.returncode != 0
    assert not Path(env["MAIL_CALLS"]).exists()


def test_insecure_one_shot_token_is_rejected(tmp_path: Path):
    work = tmp_path / "work"; work.mkdir()
    env = _env(tmp_path); env["MAIL_CALLS"] = str(tmp_path / "mail")
    _publish(env, work, "ParentCurie", "parent-token")
    one_shot = tmp_path / "one-shot"; one_shot.write_text("child-token"); one_shot.chmod(0o644)
    result = _run(env, work,
        'ags_prepare_preregistered_handoff ChildCurie "$PWD" "$1" ParentCurie team-x', str(one_shot))
    assert result.returncode != 0
    assert not Path(env["MAIL_CALLS"]).exists()


def test_delegate_preregistration_binds_the_declared_workdir():
    text = SKILL.read_text(encoding="utf-8")
    block = text.split('CHILD_NAME="$("${AGENTSTACK_PREREGISTER_CHILD', 1)[1].split(')"', 1)[0]
    assert '--work-dir "<working-directory>"' in block
