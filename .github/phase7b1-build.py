from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REF = "5c061e67318724bf5102f12cd9dfecbccc4c7cd7"
SERVER = Path("dashboard/server.py")
TEST = Path("tests/test_dashboard_session_attribution.py")


def ref_source() -> str:
    return subprocess.check_output(
        ["git", "show", f"{REF}:dashboard/server.py"], text=True
    )


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "".join(lines[node.lineno - 1 : node.end_lineno]).rstrip() + "\n"
    raise RuntimeError(f"function not found: {name}")


def function_span(source: str, name: str) -> tuple[int, int]:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return offsets[node.lineno - 1], offsets[node.end_lineno]
    raise RuntimeError(f"function not found: {name}")


def insert_after_function(source: str, name: str, block: str) -> str:
    _start, end = function_span(source, name)
    return source[:end] + "\n\n" + block.rstrip() + "\n" + source[end:]


def insert_before_function(source: str, name: str, block: str) -> str:
    start, _end = function_span(source, name)
    return source[:start] + block.rstrip() + "\n\n" + source[start:]


reference = ref_source()
current = SERVER.read_text(encoding="utf-8")

cache_anchor = 'SIGNALS_DIR = _env_path("AGENTSTACK_SIGNALS_DIR", os.path.join(MAIL_HOME, "signals"))\n'
if current.count(cache_anchor) != 1:
    raise RuntimeError("SIGNALS_DIR anchor drifted")
if "_PROJECT_KEY_CACHE" not in current:
    current = current.replace(
        cache_anchor,
        cache_anchor + '_PROJECT_KEY_CACHE: dict = {"signature": None, "value": ""}\n',
        1,
    )

helper_names = [
    "_normalize_project_key_value",
    "_project_key",
    "_project_context_helper",
    "_context_helper_result",
    "_context_helper_value",
    "_resolved_work_dir_context",
    "_configured_workspace",
    "_context_matches_dashboard_project",
    "_cwd_matches_dashboard_project",
]
helper_parts: list[str] = []
for name in helper_names:
    part = function_source(reference, name)
    if name == "_project_key":
        part = part.replace(
            "def _project_key() -> str:",
            "def _canonical_dashboard_project_key() -> str:",
            1,
        )
    if name in {"_context_helper_result", "_context_matches_dashboard_project"}:
        part = part.replace("_project_key()", "_canonical_dashboard_project_key()")
    helper_parts.append(part)
helper_block = "\n\n".join(helper_parts)
if "def _canonical_dashboard_project_key" not in current:
    current = insert_after_function(current, "_project_key", helper_block)

sidecar = function_source(reference, "_runtime_agent_token_project")
if "def _runtime_agent_token_project" not in current:
    current = insert_after_function(current, "_runtime_agent_token", sidecar)

attribution_names = [
    "_runtime_name_binding_project",
    "_tmux_session_env",
    "_session_matches_dashboard_project",
    "_live_session_matches_dashboard_project",
    "_live_session_conflicts_with_dashboard",
]
attribution_parts: list[str] = [
    '# Never equal to a normalized project key: NUL cannot occur in one.\n_DURABLE_PROJECT_CONFLICT = "\\0conflicting-durable-project"\n'
]
for name in attribution_names:
    part = function_source(reference, name)
    if name == "_session_matches_dashboard_project":
        part = part.replace("_project_key()", "_canonical_dashboard_project_key()")
    attribution_parts.append(part)
attribution_block = "\n\n".join(attribution_parts)
if "def _session_matches_dashboard_project" not in current:
    current = insert_before_function(current, "classify", attribution_block)

ast.parse(current)
SERVER.write_text(current, encoding="utf-8")

TEST.write_text(
    r'''"""Project attribution primitives for Dashboard live tmux sessions."""
from __future__ import annotations

import json
from pathlib import Path

from dashboard import server


def _session_env(values: dict[str, str]):
    def read(_name: str, variable: str) -> str:
        return values.get(variable, "")
    return read


def test_canonical_dashboard_key_resolves_configured_repository(monkeypatch, tmp_path: Path):
    configured = tmp_path / "worktree"
    configured.mkdir()
    monkeypatch.setattr(server, "PROJECT_KEY", str(configured))
    monkeypatch.setattr(server, "VAULT", "")
    monkeypatch.setattr(server, "_PROJECT_KEY_CACHE", {"signature": None, "value": ""})
    monkeypatch.delenv("AGENTSTACK_PROJECT_KEY", raising=False)
    monkeypatch.delenv("PROJECT_KEY", raising=False)
    monkeypatch.delenv("AGENTSTACK_PROJECT_CONTEXT", raising=False)
    monkeypatch.delenv("AGENTSTACK_PROJECT_REPOSITORY", raising=False)
    monkeypatch.delenv("AGENTSTACK_PROJECT_WORK_DIR", raising=False)

    def resolver(action: str, *args: str, configured_fallback: bool = False):
        assert action == "resolve-project-key"
        return True, "/canonical/repository"

    monkeypatch.setattr(server, "_context_helper_result", resolver)
    assert server._canonical_dashboard_project_key() == "/canonical/repository"
    # Phase 7b1 is primitive-only: existing reads are not switched yet.
    assert server._project_key() == str(configured)


def test_session_key_mismatch_fails_before_path_fallback(monkeypatch):
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(
        server,
        "_tmux_session_env",
        _session_env({"AGENTSTACK_PROJECT_KEY": "/project/B"}),
    )
    monkeypatch.setattr(server, "_runtime_name_binding_project", lambda _name: "")
    monkeypatch.setattr(
        server,
        "_cwd_matches_dashboard_project",
        lambda _cwd: (_ for _ in ()).throw(AssertionError("cwd fallback must not run")),
    )
    assert not server._session_matches_dashboard_project("BlueLake", {"cwd": ""})


def test_matching_session_key_is_sufficient_without_legacy_paths(monkeypatch):
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(
        server,
        "_tmux_session_env",
        _session_env({"AGENTSTACK_PROJECT_KEY": "/project/A"}),
    )
    monkeypatch.setattr(server, "_runtime_name_binding_project", lambda _name: "")
    assert server._session_matches_dashboard_project("BlueLake", {"cwd": ""})


def test_durable_project_conflict_fails_closed(monkeypatch):
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(server, "_tmux_session_env", _session_env({}))
    monkeypatch.setattr(
        server,
        "_runtime_name_binding_project",
        lambda _name: server._DURABLE_PROJECT_CONFLICT,
    )
    assert not server._session_matches_dashboard_project("BlueLake", {"cwd": ""})


def test_matching_key_does_not_override_mismatched_bound_path(monkeypatch, tmp_path: Path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(
        server,
        "_tmux_session_env",
        _session_env({
            "AGENTSTACK_PROJECT_KEY": "/project/A",
            "AGENTSTACK_PROJECT_REPOSITORY": str(foreign),
        }),
    )
    monkeypatch.setattr(server, "_runtime_name_binding_project", lambda _name: "/project/A")
    monkeypatch.setattr(server, "_cwd_matches_dashboard_project", lambda _cwd: False)
    assert not server._session_matches_dashboard_project("BlueLake", {"cwd": ""})


def test_legacy_session_can_pass_on_verified_cwd_only(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(server, "_tmux_session_env", _session_env({}))
    monkeypatch.setattr(server, "_runtime_name_binding_project", lambda _name: "")
    monkeypatch.setattr(
        server,
        "_cwd_matches_dashboard_project",
        lambda cwd: cwd == str(workspace.resolve()),
    )
    assert server._session_matches_dashboard_project(
        "BlueLake", {"cwd": str(workspace)}
    )


def test_session_without_any_project_evidence_fails_closed(monkeypatch):
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(server, "_tmux_session_env", _session_env({}))
    monkeypatch.setattr(server, "_runtime_name_binding_project", lambda _name: "")
    assert not server._session_matches_dashboard_project("BlueLake", {"cwd": ""})


def test_path_eligibility_cache_reuses_same_resolved_path(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(server, "_canonical_dashboard_project_key", lambda: "/project/A")
    monkeypatch.setattr(
        server,
        "_tmux_session_env",
        _session_env({
            "AGENTSTACK_PROJECT_KEY": "/project/A",
            "AGENTSTACK_PROJECT_REPOSITORY": str(workspace),
            "AGENTSTACK_PROJECT_WORK_DIR": str(workspace),
        }),
    )
    monkeypatch.setattr(server, "_runtime_name_binding_project", lambda _name: "")
    calls: list[str] = []
    monkeypatch.setattr(
        server,
        "_cwd_matches_dashboard_project",
        lambda cwd: calls.append(cwd) is None or True,
    )
    cache: dict[str, bool] = {}
    assert server._session_matches_dashboard_project(
        "BlueLake", {"cwd": str(workspace)}, cache
    )
    assert calls == [str(workspace.resolve())]
    assert cache == {str(workspace.resolve()): True}


def test_runtime_name_binding_detects_disagreeing_durable_projects(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "RUNTIME_DIR", str(tmp_path))
    name = "BlueLake"
    binding_dir = tmp_path / "name-bindings"
    binding_dir.mkdir()
    binding = binding_dir / f"{server._agent_name_comparison_key(name)}.json"
    binding.write_text(
        json.dumps({"agent_name": name, "project_key": "/project/A"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_runtime_agent_token_project", lambda _name: None)
    assert server._runtime_name_binding_project(name) == "/project/A"

    child_dir = tmp_path / "child-agents"
    child_dir.mkdir()
    (child_dir / f"{name}.json").write_text(
        json.dumps({"agent_name": name, "project_key": "/project/B"}),
        encoding="utf-8",
    )
    assert server._runtime_name_binding_project(name) == server._DURABLE_PROJECT_CONFLICT


def test_runtime_token_project_distinguishes_legacy_absence_from_invalid(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "RUNTIME_DIR", str(tmp_path))
    name = "BlueLake"
    assert server._runtime_agent_token_project(name) is None

    sidecar = tmp_path / "agent_token_BlueLake.project"
    sidecar.write_text("/project/A\n", encoding="utf-8")
    assert server._runtime_agent_token_project(name) == "/project/A"

    sidecar.write_text("\n", encoding="utf-8")
    assert server._runtime_agent_token_project(name) == ""
''',
    encoding="utf-8",
)

subprocess.run(["python3", "-m", "py_compile", str(SERVER)], check=True)
