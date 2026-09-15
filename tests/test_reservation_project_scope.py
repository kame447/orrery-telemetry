"""Actual hook cwd, not installed or process context, defines reservation scope."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_check_file_reservation import _Server, _mcp_result

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def repos(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('AGENTSTACK_', 'GIT_')) and key not in
           {'HOME', 'PROJECT_KEY', 'AGENT_NAME', 'PARENT_AGENT', 'CHILD_REGISTRATION_TOKEN',
            'TMUX', 'TMUX_PANE', 'MCP_URL', 'MCP_AGENT_MAIL_TOKEN', 'BASH_ENV', 'ENV'}}
    home = tmp_path / 'home'
    home.mkdir()
    env['HOME'] = str(home)
    first, second = tmp_path / 'first', tmp_path / 'second'
    for repo in (first, second):
        subprocess.run(['git', 'init', '-q', str(repo)], env=env, check=True)
    return first.resolve(), second.resolve(), env


def run_hook(repos, url, payload, *, process_cwd=None, roots=None):
    first, second, env = repos
    env = dict(env)
    env.update(AGENTSTACK_PROJECT_KEY=str(first),
               AGENTSTACK_PROTECTED_ROOTS=str(roots or first),
               AGENTSTACK_PROJECT_CONTEXT='1',
               AGENTSTACK_HOOKS_DIR=str(ROOT / 'hooks'),
               AGENTSTACK_RUNTIME_DIR=str(first.parent / 'runtime'),
               AGENTSTACK_MCP_URL=url, AGENTSTACK_MAIL_HTTP_BEARER_MODE='disabled',
               AGENT_NAME='BrightCurie', FILE_RESERVATION_RETRY_DELAY_SECONDS='0',
               GIT_DIR=str(first / '.git'), GIT_WORK_TREE=str(first))
    return subprocess.run(['/bin/bash', str(ROOT / 'hooks/check-file-reservation.sh')],
        input=json.dumps(payload), cwd=process_cwd or first, env=env,
        text=True, capture_output=True, timeout=20)


def arguments(server):
    return [request['json']['params']['arguments'] for request in server.requests]


def test_stale_installed_roots_cannot_bypass_the_actual_repository(repos):
    first, second, _ = repos
    with _Server(lambda _: (200, _mcp_result(0))) as server:
        result = run_hook(repos, server.url,
            {'cwd': str(second), 'tool_input': {'file_path': str(second / 'note.md')}})
    assert result.returncode == 2, result.stderr
    assert 'FILE RESERVATION REQUIRED' in result.stderr
    assert server.requests
    assert all(item['project_key'] == str(second) for item in arguments(server))


def test_another_repository_is_not_renewed_under_the_current_namespace(repos):
    first, second, _ = repos
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run_hook(repos, server.url,
            {'cwd': str(second), 'tool_input': {'file_path': str(first / 'note.md')}})
    assert result.returncode == 0, result.stderr
    assert server.requests == []


def test_relative_file_uses_payload_cwd_not_process_cwd(repos):
    first, second, _ = repos
    sub = second / 'sub'
    sub.mkdir()
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run_hook(repos, server.url,
            {'cwd': str(sub), 'tool_input': {'file_path': 'note.md'}}, roots=second)
    assert result.returncode == 0, result.stderr
    assert len(server.requests) == 1
    args = arguments(server)[0]
    assert args['project_key'] == str(second)
    assert args['paths'] == ['sub/note.md', str(sub / 'note.md')]


def test_linked_worktree_has_its_own_protected_root_and_shared_namespace(repos):
    first, second, env = repos
    subprocess.run(['git', '-C', str(second), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit',
                    '--allow-empty', '-qm', 'fixture'], env=env, check=True)
    linked = second.parent / 'linked'
    subprocess.run(['git', '-C', str(second), 'worktree', 'add', '-qb', 'linked', str(linked)],
                   env=env, check=True)
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run_hook(repos, server.url,
            {'cwd': str(linked), 'tool_input': {'file_path': str(linked / 'note.md')}})
    assert result.returncode == 0, result.stderr
    assert len(server.requests) == 1
    assert arguments(server)[0]['project_key'] == str(second)
    assert arguments(server)[0]['paths'] == ['note.md', str(linked.resolve() / 'note.md')]


@pytest.mark.parametrize('kind', ['absent', 'deleted'])
def test_invalid_cwd_cannot_hide_behind_outside_stale_roots(repos, kind):
    _, second, _ = repos
    payload = {'tool_input': {'file_path': str(second / 'note.md')}}
    if kind == 'deleted':
        payload['cwd'] = str(second / 'missing')
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run_hook(repos, server.url, payload)
    assert result.returncode == 2
    assert 'AGENT PROJECT CONTEXT UNRESOLVED' in result.stderr
    assert server.requests == []


def test_symlink_outside_workspace_does_not_touch_its_namespace(repos):
    first, second, _ = repos
    (second / 'outside').symlink_to(first, target_is_directory=True)
    with _Server(lambda _: (200, _mcp_result(1))) as server:
        result = run_hook(repos, server.url,
            {'cwd': str(second), 'tool_input': {'file_path': str(second / 'outside/note.md')}},
            roots=second)
    assert result.returncode == 0, result.stderr
    assert server.requests == []
