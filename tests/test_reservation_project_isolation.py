"""The reservation hooks act only in the hook invocation's actual workspace.

Every hook here resolves the workspace from the payload cwd, validates the
selected project against it, and only then classifies paths or talks to Mail.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_check_file_reservation import _Server, _mcp_result

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
CHECK = HOOKS / "check-file-reservation.sh"
RELEASE = HOOKS / "release-file-reservation.sh"
RELEASE_ALL = HOOKS / "release-all-reservations.sh"
INVALIDATE = HOOKS / "invalidate-release-debounce.sh"
AGENT = "BrightCurie"


def _released(_count):
    body = json.dumps({"jsonrpc": "2.0", "id": "test",
                       "result": {"isError": False, "structuredContent": {"released": 1}}})
    return 200, body.encode()


@pytest.fixture
def repos(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENTSTACK_", "GIT_"))
        and key
        not in {
            "HOME", "PROJECT_KEY", "AGENT_NAME", "PARENT_AGENT",
            "CHILD_REGISTRATION_TOKEN", "TMUX", "TMUX_PANE", "MCP_URL",
            "MCP_AGENT_MAIL_TOKEN", "BASH_ENV", "ENV",
        }
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    first, second = tmp_path / "first", tmp_path / "second"
    for repo in (first, second):
        subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return {
        "first": first.resolve(),
        "second": second.resolve(),
        "env": env,
        "runtime": runtime,
        "home": home,
    }


def run(ctx, hook, url, payload, *, process_cwd=None, **overrides):
    env = dict(ctx["env"])
    env.update(
        AGENTSTACK_HOOKS_DIR=str(HOOKS),
        AGENTSTACK_RUNTIME_DIR=str(ctx["runtime"]),
        AGENTSTACK_MCP_URL=url,
        AGENTSTACK_MAIL_HTTP_BEARER_MODE="disabled",
        AGENTSTACK_RELEASE_GRACE_SECONDS="0",
        AGENT_NAME=AGENT,
        FILE_RESERVATION_RETRY_DELAY_SECONDS="0",
    )
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(payload),
        cwd=process_cwd or ctx["home"],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def edit(cwd, file_path, **extra):
    document = {"session_id": "session-1", "tool_input": {"file_path": str(file_path)}}
    if cwd is not None:
        document["cwd"] = str(cwd)
    document.update(extra)
    return document


def arguments(server):
    return [request["json"]["params"]["arguments"] for request in server.requests]


def failure_log(ctx):
    path = ctx["runtime"] / "release-failures.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def slot(project_key, agent, relative):
    return hashlib.sha1("\0".join((project_key, agent, relative)).encode()).hexdigest()


def linked_worktree(ctx, repo, name):
    env = ctx["env"]
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture"],
        env=env, check=True,
    )
    linked = repo.parent / name
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-qb", name, str(linked)],
                   env=env, check=True)
    return linked.resolve()


# --- stale ambient selection ------------------------------------------------

def test_stale_ambient_key_and_roots_block_edits_in_another_repository(repos):
    first, second = repos["first"], repos["second"]
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        for target in (second / "note.md", first / "note.md", "/elsewhere/note.md"):
            result = run(
                repos, CHECK, server.url, edit(second, target),
                AGENTSTACK_PROJECT_KEY=first, AGENTSTACK_PROTECTED_ROOTS=first,
                GIT_DIR=first / ".git", GIT_WORK_TREE=first,
            )
            assert result.returncode == 2, (target, result.stderr)
            assert "AGENT PROJECT CONTEXT MISMATCH" in result.stderr
    assert server.requests == []


def test_stale_installed_key_is_validated_like_a_live_one(repos):
    first, second = repos["first"], repos["second"]
    env_dir = repos["home"] / ".agentstack"
    env_dir.mkdir()
    (env_dir / "env.sh").write_text(
        f"export AGENTSTACK_PROJECT_KEY={first}\nexport AGENTSTACK_PROTECTED_ROOTS={first}\n",
        encoding="utf-8",
    )
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        stale = run(repos, CHECK, server.url, edit(second, second / "note.md"))
        assert stale.returncode == 2
        assert "AGENT PROJECT CONTEXT MISMATCH" in stale.stderr
        assert server.requests == []
        valid = run(repos, CHECK, server.url, edit(first, first / "note.md"))
    assert valid.returncode == 0, valid.stderr
    assert arguments(server) and arguments(server)[0]["project_key"] == str(first)


def test_stale_configured_root_cannot_replace_the_actual_workspace(repos):
    first, second = repos["first"], repos["second"]
    with _Server(lambda _: (200, _mcp_result(0))) as server:
        guarded = run(
            repos, CHECK, server.url, edit(second, second / "note.md"),
            AGENTSTACK_PROJECT_KEY=second, AGENTSTACK_PROTECTED_ROOTS=first,
        )
        assert guarded.returncode == 2
        assert "FILE RESERVATION REQUIRED" in guarded.stderr
        assert {item["project_key"] for item in arguments(server)} == {str(second)}
        before = len(server.requests)
        # The stale root names another repository: never renewed under ours.
        foreign = run(
            repos, CHECK, server.url, edit(second, first / "note.md"),
            AGENTSTACK_PROJECT_KEY=second, AGENTSTACK_PROTECTED_ROOTS=first,
        )
    assert foreign.returncode == 0, foreign.stderr
    assert len(server.requests) == before


# --- payload cwd, not the hook process directory ----------------------------

def test_payload_cwd_not_shell_cwd_selects_workspace_and_relative_base(repos):
    first, second = repos["first"], repos["second"]
    sub = second / "sub"
    sub.mkdir()
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run(repos, CHECK, server.url, edit(sub, "note.md"), process_cwd=first)
    assert result.returncode == 0, result.stderr
    assert arguments(server) == [{
        "project_key": str(second),
        "agent_name": AGENT,
        "paths": ["sub/note.md", str(sub / "note.md")],
        "extend_seconds": 900,
    }]


def test_missing_payload_cwd_is_unresolved_even_for_a_valid_relative_edit(repos):
    second = repos["second"]
    payload = edit(None, "note.md", tool_response={"success": True})
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        # The hook directory and the selected key agree, and still they are
        # not the session's workspace: a relative path has no base.
        guarded = run(repos, CHECK, server.url, payload,
                      process_cwd=second, AGENTSTACK_PROJECT_KEY=second)
        released = run(repos, RELEASE, server.url, payload,
                       process_cwd=second, AGENTSTACK_PROJECT_KEY=second)
    assert guarded.returncode == 2, guarded.stderr
    assert "AGENT PROJECT CONTEXT UNRESOLVED" in guarded.stderr
    assert released.returncode == 0, released.stderr
    assert server.requests == []
    assert "release session=session-1 error=project-context-invalid" in failure_log(repos)


def test_missing_cwd_with_stale_shell_cwd_cannot_turn_real_edits_into_no_ops(repos):
    first, second = repos["first"], repos["second"]
    payload = edit(None, second / "note.md", tool_response={"success": True})
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        for overrides in ({}, {"AGENTSTACK_PROJECT_KEY": first}):
            guarded = run(repos, CHECK, server.url, payload, process_cwd=first, **overrides)
            assert guarded.returncode == 2, (overrides, guarded.stderr)
            assert "AGENT PROJECT CONTEXT UNRESOLVED" in guarded.stderr
            released = run(repos, RELEASE, server.url, payload, process_cwd=first, **overrides)
            assert released.returncode == 0, released.stderr
    assert server.requests == []
    assert failure_log(repos).count("error=project-context-invalid") == 2


def test_session_hooks_never_act_without_a_payload_cwd(repos):
    first = repos["first"]
    state = repos["runtime"] / "file_release_debounce"
    state.mkdir()
    armed = state / slot(str(first), AGENT, "note.md")
    armed.write_text("token\n", encoding="utf-8")
    invalidate = {"session_id": "session-1", "tool_input": {"paths": ["note.md"]}}
    with _Server(_released) as server:
        for overrides in ({}, {"AGENTSTACK_PROJECT_KEY": first}):
            ended = run(repos, RELEASE_ALL, server.url, {"session_id": "session-1"},
                        process_cwd=first, **overrides)
            assert ended.returncode == 0, ended.stderr
            invalidated = run(repos, INVALIDATE, server.url, invalidate,
                              process_cwd=first, **overrides)
            assert invalidated.returncode == 0, invalidated.stderr
    assert server.requests == []
    assert failure_log(repos).count("release-all session=session-1 error=project-context-invalid") == 2
    assert armed.exists()


# --- canonical namespace ------------------------------------------------------

def test_selected_key_spellings_resolve_to_the_canonical_namespace(repos):
    second = repos["second"]
    alias = repos["home"] / "alias"
    alias.symlink_to(second, target_is_directory=True)
    (second / "sub").mkdir()
    spellings = (alias, f"{second}/sub/..", f"{alias}/sub/../")
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        for spelling in spellings:
            result = run(repos, CHECK, server.url, edit(second, second / "note.md"),
                         AGENTSTACK_PROJECT_KEY=spelling)
            assert result.returncode == 0, (spelling, result.stderr)
        with _Server(_released) as release_server:
            ended = run(repos, RELEASE_ALL, release_server.url,
                        {"session_id": "session-1", "cwd": str(alias)},
                        AGENTSTACK_PROJECT_KEY=alias)
    assert ended.returncode == 0, ended.stderr
    assert [item["project_key"] for item in arguments(server)] == [str(second)] * len(spellings)
    assert arguments(release_server) == [{"project_key": str(second), "agent_name": AGENT}]


def test_invalidation_leaves_project_less_legacy_slots_alone(repos):
    first = repos["first"]
    state = repos["runtime"] / "file_release_debounce"
    state.mkdir()
    legacy = state / hashlib.sha1(f"{AGENT}\0note.md".encode()).hexdigest()
    legacy.write_text("token\n", encoding="utf-8")
    current = state / slot(str(first), AGENT, "note.md")
    current.write_text("token\n", encoding="utf-8")
    result = run(repos, INVALIDATE, "http://127.0.0.1:9/mcp",
                 {"session_id": "session-1", "cwd": str(first),
                  "tool_input": {"paths": ["note.md"]}})
    assert result.returncode == 0, result.stderr
    assert legacy.exists()
    assert not current.exists()


# --- linked worktrees ---------------------------------------------------------

@pytest.mark.parametrize("selected", ["canonical", "derived"])
def test_linked_worktree_shares_namespace_but_protects_its_own_tree(repos, selected):
    second = repos["second"]
    linked = linked_worktree(repos, second, "linked")
    overrides = {"AGENTSTACK_PROTECTED_ROOTS": linked}
    if selected == "canonical":
        overrides["AGENTSTACK_PROJECT_KEY"] = second
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run(repos, CHECK, server.url, edit(linked, linked / "note.md"), **overrides)
        # The main checkout is another worktree, not this workspace.
        other = run(repos, CHECK, server.url, edit(linked, second / "note.md"), **overrides)
    assert result.returncode == 0, result.stderr
    assert other.returncode == 0, other.stderr
    assert arguments(server) == [{
        "project_key": str(second),
        "agent_name": AGENT,
        "paths": ["note.md", str(linked / "note.md")],
        "extend_seconds": 900,
    }]


def test_configured_sibling_worktree_root_of_the_same_repository_is_protected(repos):
    second = repos["second"]
    linked = linked_worktree(repos, second, "linked")
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run(repos, CHECK, server.url, edit(linked, second / "doc.md"),
                     AGENTSTACK_PROJECT_KEY=second,
                     AGENTSTACK_PROTECTED_ROOTS=f"{linked}:{second}")
    assert result.returncode == 0, result.stderr
    assert arguments(server)[0]["paths"] == ["doc.md", str(second / "doc.md")]


# --- explicit logical namespace ---------------------------------------------

def test_logical_namespace_needs_matching_repository_provenance(repos):
    first, second = repos["first"], repos["second"]
    payload = edit(second, second / "note.md")
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        valid = run(repos, CHECK, server.url, payload,
                    AGENTSTACK_PROJECT_KEY="team-alpha",
                    AGENTSTACK_PROJECT_REPOSITORY=second)
        assert valid.returncode == 0, valid.stderr
        assert arguments(server)[0]["project_key"] == "team-alpha"
        for repository in (first, None):
            result = run(repos, CHECK, server.url, payload,
                         AGENTSTACK_PROJECT_KEY="team-alpha",
                         AGENTSTACK_PROJECT_REPOSITORY=repository)
            assert result.returncode == 2, repository
            assert "AGENT PROJECT CONTEXT MISMATCH" in result.stderr
    assert len(server.requests) == 1


# --- release hooks never touch another project -------------------------------

def test_release_hooks_skip_a_mismatched_project_before_the_network(repos):
    first, second = repos["first"], repos["second"]
    with _Server(_released) as server:
        released = run(
            repos, RELEASE, server.url,
            edit(second, second / "note.md", tool_response={"success": True}),
            AGENTSTACK_PROJECT_KEY=first,
        )
        ended = run(repos, RELEASE_ALL, server.url,
                    {"session_id": "session-1", "cwd": str(second)},
                    AGENTSTACK_PROJECT_KEY=first)
    assert released.returncode == 0, released.stderr
    assert ended.returncode == 0, ended.stderr
    assert server.requests == []
    log = failure_log(repos)
    assert "release session=session-1 error=project-context-invalid" in log
    assert "release-all session=session-1 error=project-context-invalid" in log


def test_release_all_uses_the_validated_workspace_namespace(repos):
    first, second = repos["first"], repos["second"]
    with _Server(_released) as server:
        result = run(repos, RELEASE_ALL, server.url,
                     {"session_id": "session-1", "cwd": str(second)},
                     process_cwd=first)
    assert result.returncode == 0, result.stderr
    assert arguments(server) == [{"project_key": str(second), "agent_name": AGENT}]


def test_invalidation_with_mismatched_project_deletes_nothing(repos):
    first, second = repos["first"], repos["second"]
    state = repos["runtime"] / "file_release_debounce"
    state.mkdir()
    armed = state / slot(str(first), AGENT, "note.md")
    armed.write_text("token\n", encoding="utf-8")
    result = run(repos, INVALIDATE, "http://127.0.0.1:9/mcp",
                 {"session_id": "session-1", "cwd": str(second),
                  "tool_input": {"paths": ["note.md"]}},
                 AGENTSTACK_PROJECT_KEY=first)
    assert result.returncode == 0, result.stderr
    assert armed.exists()


def test_same_agent_and_path_in_two_projects_have_separate_release_slots(repos):
    first, second = repos["first"], repos["second"]
    state = repos["runtime"] / "file_release_debounce"
    with _Server(_released) as server:
        for repo in (first, second):
            # No worker is installed next to these hooks, so each release runs
            # immediately after arming its slot; the slot file stays behind.
            result = run(repos, RELEASE, server.url,
                         edit(repo, repo / "note.md", tool_response={"success": True}),
                         AGENTSTACK_HOOKS_DIR=repos["home"],
                         AGENTSTACK_RELEASE_GRACE_SECONDS="90")
            assert result.returncode == 0, result.stderr
    first_slot = state / slot(str(first), AGENT, "note.md")
    second_slot = state / slot(str(second), AGENT, "note.md")
    assert sorted(path.name for path in state.iterdir()) == sorted(
        [first_slot.name, second_slot.name])
    assert [item["project_key"] for item in arguments(server)] == [str(first), str(second)]

    result = run(repos, INVALIDATE, "http://127.0.0.1:9/mcp",
                 {"session_id": "session-1", "cwd": str(first),
                  "tool_input": {"paths": ["note.md"]}})
    assert result.returncode == 0, result.stderr
    assert not first_slot.exists()
    assert second_slot.exists()


# --- path normalization -------------------------------------------------------

def test_symlink_and_dotdot_spellings_cannot_masquerade_as_protected_paths(repos):
    first, second = repos["first"], repos["second"]
    (second / "outside").symlink_to(first, target_is_directory=True)
    spellings = (second / "outside" / "note.md", f"{second}/../first/note.md",
                 "outside/note.md", "../first/note.md")
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        for spelling in spellings:
            for hook, extra in ((CHECK, {}), (RELEASE, {"tool_response": {"success": True}})):
                result = run(repos, hook, server.url, edit(second, spelling, **extra),
                             AGENTSTACK_PROJECT_KEY=second)
                assert result.returncode == 0, (spelling, result.stderr)
    assert server.requests == []
    assert failure_log(repos) == ""


def test_symlinked_spelling_into_the_workspace_is_guarded_by_its_real_path(repos):
    second = repos["second"]
    alias = repos["home"] / "alias"
    alias.symlink_to(second, target_is_directory=True)
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run(repos, CHECK, server.url,
                     edit(alias, alias / "docs" / ".." / "note.md"))
    assert result.returncode == 0, result.stderr
    assert arguments(server)[0]["project_key"] == str(second)
    assert arguments(server)[0]["paths"] == ["note.md", str(second / "note.md")]
