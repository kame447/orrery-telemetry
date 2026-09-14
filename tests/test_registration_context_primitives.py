"""Non-activating registration/context API contracts; no real Mail or user HOME."""
import json
import os
import subprocess
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'bin/lib/agentstack-register.sh'
@pytest.fixture
def workspace(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('AGENTSTACK_', 'GIT_')) and key not in
           {'HOME', 'PROJECT_KEY', 'AGENT_NAME', 'PARENT_AGENT', 'CHILD_REGISTRATION_TOKEN',
            'TMUX', 'TMUX_PANE', 'MCP_URL', 'MCP_AGENT_MAIL_TOKEN', 'BASH_ENV', 'ENV'}}
    env.update(HOME=str(home), AGENTSTACK_RUNTIME_DIR=str(tmp_path / 'runtime'),
               AGENTSTACK_PROJECT_KEY='stale-project', AGENTSTACK_PROJECT_CONTEXT='1')
    repo = tmp_path / 'repository'
    subprocess.run(['git', 'init', '-q', str(repo)], env=env, check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', 'commit',
                    '--allow-empty', '-qm', 'fixture'], env=env, check=True)
    return repo.resolve(), env
def shell(workspace, body, *args):
    repo, env = workspace
    return subprocess.run(['/bin/bash', '-c',
        'set -euo pipefail; . "$1"; shift; '
        'ags_mcp_call() { echo "unexpected Mail call" >&2; return 99; }; ' + body,
        'test', str(LIB), *map(str, args)], cwd=repo, env=env,
        capture_output=True, text=True, timeout=20)
def test_explicit_transport_revalidates_workspace(workspace):
    repo, _ = workspace
    result = shell(workspace,
        'ctx=$(agentstack_resolve_invocation_context "$PWD" team-x); '
        'wire=$(agentstack_build_invocation_transport "$ctx" team-x); '
        'agentstack_validate_invocation_transport "$wire" "$PWD"')
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['project_key'] == 'team-x'
    assert data['repository_key'] == str(repo)
    assert data['work_dir'] == str(repo)
    assert data['protected_roots'] == [str(repo)]
def test_transport_cannot_authorize_another_repository(workspace, tmp_path):
    other = tmp_path / 'other'
    subprocess.run(['git', 'init', '-q', str(other)], env=workspace[1], check=True)
    result = shell(workspace,
        'ctx=$(agentstack_resolve_invocation_context "$PWD" team-x); '
        'wire=$(agentstack_build_invocation_transport "$ctx" team-x); '
        'agentstack_validate_invocation_transport "$wire" "$1"', other)
    assert result.returncode != 0
    assert not (tmp_path / 'runtime').exists()
def publish_body():
    return ('ctx=$(agentstack_resolve_invocation_context "$PWD" team-x); '
            'ags_begin_registration_ownership "$ctx" BrightCurie fixture-token top-level candidate; '
            'ags_commit_registration_ownership "$ctx" BrightCurie BrightCurie fixture-token top-level; ')
def test_owner_allows_linked_worktree_but_rejects_independent_clone(workspace, tmp_path):
    repo, env = workspace
    linked, clone = tmp_path / 'linked', tmp_path / 'clone'
    subprocess.run(['git', '-C', str(repo), 'worktree', 'add', '-qb', 'fixture-linked', str(linked)], env=env, check=True)
    subprocess.run(['git', 'clone', '-q', str(repo), str(clone)], env=env, check=True)
    result = shell(workspace, publish_body() +
        'ags_registration_owner_context BrightCurie fixture-token "$1"; '
        'if ags_registration_owner_context BrightCurie fixture-token "$2"; then exit 93; fi; '
        'if ags_registration_owner_context BrightCurie wrong-token "$1"; then exit 94; fi', linked, clone)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['project_key'] == 'team-x'
    assert data['repository_key'] == str(repo)
    assert data['work_dir'] == str(linked.resolve())
    assert data['protected_roots'] == [str(linked.resolve())]
    runtime = Path(env['AGENTSTACK_RUNTIME_DIR'])
    assert (runtime / 'agent_owner_BrightCurie.json').stat().st_mode & 0o077 == 0
    assert (runtime / 'agent_token_BrightCurie').stat().st_mode & 0o077 == 0
    assert not list(runtime.glob('*.pending'))
    assert 'fixture-token' not in result.stdout
def test_name_aliases_share_one_pending_claim(workspace):
    result = shell(workspace,
        'ctx=$(agentstack_resolve_invocation_context "$PWD"); '
        'ags_begin_registration_ownership "$ctx" Bright-Curie fixture-token top-level candidate; '
        'if ags_begin_registration_ownership "$ctx" brightcurie other-token top-level candidate; '
        'then exit 95; fi; ags_release_registration_ownership')
    assert result.returncode == 0, result.stderr
    assert not list(Path(workspace[1]['AGENTSTACK_RUNTIME_DIR']).glob('*.pending'))
def test_non_git_owner_stays_inside_its_physical_root(workspace, tmp_path):
    plain = tmp_path / 'plain'
    nested = plain / 'nested'
    nested.mkdir(parents=True)
    other = tmp_path / 'unrelated'
    other.mkdir()
    result = shell(workspace,
        'cd "$1"; ' + publish_body() +
        'ags_registration_owner_context BrightCurie fixture-token "$2"; '
        'if ags_registration_owner_context BrightCurie fixture-token "$3"; then exit 96; fi', plain, nested, other)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['repository_key'] is None
def test_export_rejects_broken_context_without_partial_assignment(workspace):
    result = shell(workspace,
        'AGENTSTACK_PROJECT_KEY=before; '
        'if agentstack_export_context_json \'{"project_key":"after"}\'; then exit 97; fi; '
        'test "$AGENTSTACK_PROJECT_KEY" = before')
    assert result.returncode == 0, result.stderr
