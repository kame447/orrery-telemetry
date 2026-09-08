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

_CORE_REQUIRED_FILES = (
    "bin/lib/agentstack-register.sh",
    "hooks/project-context.sh",
)
_CORE_REQUIRED_EXECUTABLES = (
    "bin/agentstack-preregister-child",
    "hooks/cleanup-child-agent.sh",
    "integrations/codex_app/plugin/scripts/run-mcp.sh",
)


def compatible_dashboard_source(prefix: str = "# existing ORRERY dashboard\n") -> str:
    return prefix.rstrip("\n") + "\n" + _COMPATIBLE_DASHBOARD_STUB.lstrip("\n")


def seed_core_runtime_dependencies(install_dir: pathlib.Path) -> list[pathlib.Path]:
    seeded: list[pathlib.Path] = []
    for relative in _CORE_REQUIRED_FILES:
        path = install_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# core fixture\n", encoding="utf-8")
        seeded.append(path)
    for relative in _CORE_REQUIRED_EXECUTABLES:
        path = install_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        seeded.append(path)
    return seeded


def seed_existing_dashboard(
    install_dir: pathlib.Path,
    prefix: str = "# existing ORRERY dashboard\n",
) -> pathlib.Path:
    server = install_dir / "dashboard" / "server.py"
    server.parent.mkdir(parents=True, exist_ok=True)
    server.write_text(compatible_dashboard_source(prefix), encoding="utf-8")
    core_files = seed_core_runtime_dependencies(install_dir)
    manifest = install_dir / "install-state.json"
    manifest.write_text(
        json.dumps(
            {
                "owned_files": [str(server), *(str(path) for path in core_files)],
                "owned_dirs": sorted(
                    {
                        str(install_dir),
                        str(server.parent),
                        *(str(path.parent) for path in core_files),
                    }
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return server
