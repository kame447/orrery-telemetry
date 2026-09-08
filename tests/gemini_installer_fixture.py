from __future__ import annotations

import json
import pathlib


_COMPATIBLE_DASHBOARD_STUB = r'''

# Minimal static contract consumed by the optional provider overlays. These
# tests exercise installer ownership/copy behavior, not dashboard execution.
HOOKS_DIR = ""
RUNTIME_DIR = ""
SEP = "|"
SPAWN_SCRIPT = ""
_SPAWN_MODELS = {}

def _agent_program(*args, **kwargs): pass
def _has_session(*args, **kwargs): pass
def _provider_of(*args, **kwargs): pass
def _render_dashboard_index(*args, **kwargs): pass
def _tmux(*args, **kwargs): pass
def _valid(*args, **kwargs): pass
def build_agents(*args, **kwargs): pass
def classify(*args, **kwargs): pass
def do_exit(*args, **kwargs): pass
def do_resume(*args, **kwargs): pass
def do_spawn(*args, **kwargs): pass
def graph_payload(*args, **kwargs): pass
def spawn_launch_status(*args, **kwargs): pass
def spawn_names_payload(*args, **kwargs): pass
def tmux_state(*args, **kwargs): pass
'''


def compatible_dashboard_source(prefix: str = "# existing ORRERY dashboard\n") -> str:
    return prefix.rstrip("\n") + "\n" + _COMPATIBLE_DASHBOARD_STUB.lstrip("\n")


def seed_existing_dashboard(
    install_dir: pathlib.Path,
    prefix: str = "# existing ORRERY dashboard\n",
) -> pathlib.Path:
    server = install_dir / "dashboard" / "server.py"
    server.parent.mkdir(parents=True, exist_ok=True)
    server.write_text(compatible_dashboard_source(prefix), encoding="utf-8")
    manifest = install_dir / "install-state.json"
    manifest.write_text(
        json.dumps(
            {
                "owned_files": [str(server)],
                "owned_dirs": [str(server.parent), str(install_dir)],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return server
