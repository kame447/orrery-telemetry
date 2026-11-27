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
