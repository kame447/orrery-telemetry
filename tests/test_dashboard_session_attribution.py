"""Project attribution primitives for Dashboard live tmux sessions."""
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
