"""Real isolated tmux servers; a deterministic server-generation switch.

No provider or Mail service is started. The wrapper routes the first listing
through the old server, and all later commands through its replacement.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest
from dashboard import graph_data


def test_lineage_does_not_bind_reused_session_id_to_old_name(monkeypatch):
    tmux = shutil.which('tmux')
    if not tmux:
        pytest.skip('real tmux required')
    base = '/private/tmp' if Path('/private/tmp').is_dir() else None
    with tempfile.TemporaryDirectory(prefix='lineage-race-', dir=base) as path:
        root = Path(path)
        sockets = [root / 'old', root / 'new']
        def call(sock, *args, check=True):
            return subprocess.run([tmux, '-S', str(sock), '-f', '/dev/null', *args],
                                  capture_output=True, text=True, check=check, timeout=10)
        try:
            for sock, name, project in zip(sockets, ['Kid', 'Other'], ['/a', '/b']):
                call(sock, 'new-session', '-d', '-s', name, '-e', 'PARENT_AGENT=Boss',
                     '-e', f'AGENTSTACK_PROJECT_KEY={project}', 'sleep 60')
            fmt = '#{session_name}|#{session_id}|#{pid}'
            old, new = [call(sock, 'list-sessions', '-F', fmt).stdout.strip().split('|')
                        for sock in sockets]
            assert old[0] == 'Kid' and new[0] == 'Other'
            assert old[1] == new[1] == '$0' and old[2] != new[2]
            wrapper = root / 'tmux'
            quote = shlex.quote
            wrapper.write_text('#!/bin/sh\n'
                f'if [ ! -e {quote(str(root / "seen"))} ]; then\n'
                f'  : > {quote(str(root / "seen"))}\n'
                f'  exec {quote(tmux)} -S {quote(str(sockets[0]))} "$@"\n'
                'fi\n'
                f'exec {quote(tmux)} -S {quote(str(sockets[1]))} "$@"\n')
            wrapper.chmod(0o700)
            monkeypatch.setenv('PATH', str(root) + os.pathsep + os.environ['PATH'])
            monkeypatch.setattr(graph_data, '_LIVE_PARENT_CACHE', {'ts': 0.0, 'value': None})
            monkeypatch.setattr(graph_data, '_PARENT_BY_SESSION', {})
            assert graph_data._live_parents('/b') == {}
            assert 'Kid' not in graph_data._PARENT_BY_SESSION
        finally:
            for sock in sockets:
                call(sock, 'kill-server', check=False)

@pytest.mark.parametrize("before_creation", [True, False])
def test_global_project_environment_is_not_lineage_scope(monkeypatch, before_creation):
    tmux = shutil.which('tmux')
    if not tmux:
        pytest.skip('real tmux required')
    base = '/private/tmp' if Path('/private/tmp').is_dir() else None
    with tempfile.TemporaryDirectory(prefix='lineage-global-', dir=base) as path:
        root = Path(path)
        sock = root / 's'
        def call(*args, check=True):
            return subprocess.run([tmux, '-S', str(sock), '-f', '/dev/null', *args],
                                  capture_output=True, text=True, check=check, timeout=10)
        try:
            call('new-session', '-d', '-s', 'Seed', 'sleep 60')
            if before_creation:
                call('set-environment', '-g', 'AGENTSTACK_PROJECT_KEY', '/a')
            call('new-session', '-d', '-s', 'Old', '-e', 'PARENT_AGENT=Boss', 'sleep 60')
            if not before_creation:
                call('set-environment', '-g', 'AGENTSTACK_PROJECT_KEY', '/a')
            call('new-session', '-d', '-s', 'Bound', '-e', 'PARENT_AGENT=Boss',
                 '-e', 'AGENTSTACK_PROJECT_KEY=/b', 'sleep 60')
            assert call('display-message', '-p', '-t', 'Old',
                        '#{AGENTSTACK_PROJECT_KEY}').stdout.strip() == '/a'
            assert call('show-environment', '-t', '=Old', 'AGENTSTACK_PROJECT_KEY',
                        check=False).returncode != 0
            wrapper = root / 'tmux'
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(tmux) + ' -S ' +
                               shlex.quote(str(sock)) + ' "$@"\n')
            wrapper.chmod(0o700)
            monkeypatch.setenv('PATH', str(root) + os.pathsep + os.environ['PATH'])
            monkeypatch.setattr(graph_data, '_LIVE_PARENT_CACHE', {'ts': 0.0, 'value': None})
            monkeypatch.setattr(graph_data, '_PARENT_BY_SESSION', {})
            assert graph_data._live_parents('/a') == {}
            assert graph_data._live_parents('/b') == {'Bound': 'Boss'}
        finally:
            call('kill-server', check=False)


def test_cached_lineage_does_not_survive_server_replacement(monkeypatch):
    tmux = shutil.which('tmux')
    if not tmux:
        pytest.skip('real tmux required')
    base = '/private/tmp' if Path('/private/tmp').is_dir() else None
    with tempfile.TemporaryDirectory(prefix='lineage-cache-', dir=base) as path:
        root = Path(path)
        sockets = [root / 'old', root / 'new']

        def call(sock, *args, check=True):
            return subprocess.run([tmux, '-S', str(sock), '-f', '/dev/null', *args],
                                  capture_output=True, text=True, check=check, timeout=10)

        try:
            for sock, project, parent in zip(sockets, ['/a', '/b'], ['BossA', 'BossB']):
                call(sock, 'new-session', '-d', '-s', 'Kid', '-e', f'PARENT_AGENT={parent}',
                     '-e', f'AGENTSTACK_PROJECT_KEY={project}', 'sleep 60')
            fmt = '#{session_name}|#{session_id}|#{pid}'
            old, new = [call(sock, 'list-sessions', '-F', fmt).stdout.strip().split('|')
                        for sock in sockets]
            assert old[:2] == new[:2] == ['Kid', '$0'] and old[2] != new[2]
            wrapper = root / 'tmux'
            quote = shlex.quote
            wrapper.write_text('#!/bin/sh\n'
                f'if [ -e {quote(str(root / "switch"))} ]; then\n'
                f'  exec {quote(tmux)} -S {quote(str(sockets[1]))} "$@"\n'
                'fi\n'
                f'exec {quote(tmux)} -S {quote(str(sockets[0]))} "$@"\n')
            wrapper.chmod(0o700)
            monkeypatch.setenv('PATH', str(root) + os.pathsep + os.environ['PATH'])
            monkeypatch.setattr(graph_data, '_LIVE_PARENT_CACHE', {'ts': 0.0, 'value': None})
            monkeypatch.setattr(graph_data, '_PARENT_BY_SESSION', {})
            # Keep the clock fixed: expiry is not a substitute for generation validation.
            monkeypatch.setattr(graph_data, 'time', SimpleNamespace(monotonic=lambda: 100.0))
            assert graph_data._live_parents('/a') == {'Kid': 'BossA'}
            assert graph_data._LIVE_PARENT_CACHE['generations']['Kid'][0] == '$0'
            (root / 'switch').touch()
            assert graph_data._live_parents('/a') == {}
            assert graph_data._live_parents('/b') == {'Kid': 'BossB'}
        finally:
            for sock in sockets:
                call(sock, 'kill-server', check=False)
