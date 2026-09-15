from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "scripts" / "doctor.sh"
PROJECT_CONTEXT = ROOT / "hooks" / "project-context.sh"


def _git(*args: str, cwd: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def repos(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    main = tmp_path / "main"
    other = tmp_path / "other"
    linked = tmp_path / "linked"
    main.mkdir()
    other.mkdir()
    for repo in (main, other):
        _git("init", "-q", cwd=repo)
        _git(
            "-c", "user.name=Issue33 Test",
            "-c", "user.email=issue33@example.invalid",
            "commit", "--allow-empty", "-q", "-m", "initial",
            cwd=repo,
        )
    _git("worktree", "add", "-q", "-b", "issue33-linked", str(linked), cwd=main)
    return main.resolve(), linked.resolve(), other.resolve()


def _fake_command(directory: pathlib.Path, name: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    path.chmod(0o755)


def _run_doctor(
    tmp_path: pathlib.Path,
    *,
    cwd: pathlib.Path,
    project_key: pathlib.Path,
    repository: pathlib.Path,
    work_dir: pathlib.Path,
    roots: str,
    record_work_dir: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    install = home / ".agentstack"
    runtime = tmp_path / "runtime"
    (install / "hooks").mkdir(parents=True)
    (runtime / "child-agents").mkdir(parents=True)
    shutil.copy2(PROJECT_CONTEXT, install / "hooks" / "project-context.sh")
    (runtime / "child-agents" / "CrossProjectCurie.json").write_text(
        json.dumps(
            {
                "agent_name": "CrossProjectCurie",
                "project_key": str(project_key),
                "work_dir": str(record_work_dir),
                "registration_token": "doctor-owner-token-canary",
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("curl", "launchctl", "lsof", "tmux"):
        _fake_command(fake_bin, name)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "AGENTSTACK_HOME": str(install),
            "AGENTSTACK_RUNTIME_DIR": str(runtime),
            "AGENTSTACK_PROJECT_KEY": str(project_key),
            "PROJECT_KEY": str(project_key),
            "AGENTSTACK_PROJECT_CONTEXT": "1",
            "AGENTSTACK_PROJECT_REPOSITORY": str(repository),
            "AGENTSTACK_PROJECT_WORK_DIR": str(work_dir),
            "AGENTSTACK_PROJECT_WORKTREE_ROOT": str(work_dir),
            "AGENTSTACK_PROTECTED_ROOTS": roots,
        }
    )
    return subprocess.run(
        ["bash", str(DOCTOR), "--install-dir", str(install)],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_doctor_reports_cross_repository_runtime_and_protected_roots(
    tmp_path: pathlib.Path,
    repos: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    main, _linked, other = repos
    result = _run_doctor(
        tmp_path,
        cwd=main,
        project_key=main,
        repository=main,
        work_dir=main,
        roots=str(other),
        record_work_dir=other,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "CrossProjectCurie" in output
    assert "repository mismatch" in output
    assert "protected root belongs to another repository" in output
    assert "doctor-owner-token-canary" not in output


def test_doctor_accepts_linked_worktree_as_same_repository(
    tmp_path: pathlib.Path,
    repos: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
) -> None:
    main, linked, _other = repos
    nested = linked / "src" / "nested"
    nested.mkdir(parents=True)
    result = _run_doctor(
        tmp_path,
        cwd=nested,
        project_key=main,
        repository=main,
        work_dir=linked,
        roots=str(linked),
        record_work_dir=linked,
    )
    output = result.stdout + result.stderr

    assert not any(
        "warn:" in line.lower() and "CrossProjectCurie" in line
        for line in output.splitlines()
    ), output
    assert "protected roots missing current worktree root" not in output
    assert "doctor-owner-token-canary" not in output
