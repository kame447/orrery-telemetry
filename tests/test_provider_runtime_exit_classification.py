from __future__ import annotations

import signal
from types import SimpleNamespace

import dashboard.provider_classification as provider_classification
import dashboard.provider_server as server


def _reset_runtime_cache() -> None:
    provider_classification._RUNTIME_CACHE.update(
        ts=0.0,
        names=(),
        observations={},
    )


def test_gemini_runtime_is_live_while_agy_owns_the_tmux_pane():
    _reset_runtime_cache()
    assert server.classify(
        "GeminiCurie", "agy", "", True, program="antigravity"
    ) == "agent"


def test_gemini_runtime_is_finished_after_agy_exits_to_a_shell():
    _reset_runtime_cache()
    assert server.classify(
        "GeminiCurie", "zsh", "", True, program="antigravity"
    ) == "finished"


def test_wrapped_headless_agy_is_detected_below_bash():
    tree = (
        {
            101: "/bin/bash /tmp/gemini-runner.sh",
            202: (
                "/Users/test/.local/bin/agy --input-format stream-json "
                "--output-format stream-json --model gemini-3.8-flash-high"
            ),
        },
        {101: [202]},
    )

    observation = provider_classification._runtime_process_for_pane(
        101, tree, server.PROVIDER_REGISTRY
    )

    assert observation is not None
    assert observation["provider"] == "gemini"
    assert observation["runtime_command"] == "agy"
    assert observation["headless"] is True
    assert observation["pid"] == 202


def test_shell_husk_without_agy_is_not_detected_as_live():
    tree = ({101: "/bin/bash /tmp/gemini-runner.sh"}, {})

    observation = provider_classification._runtime_process_for_pane(
        101, tree, server.PROVIDER_REGISTRY
    )

    assert observation is None


def test_tmux_state_keeps_shell_command_but_classifies_wrapped_agy_live(monkeypatch):
    sep = server.SEP

    def fake_tmux(args: list[str]) -> str:
        if args[:2] == ["list-sessions", "-F"]:
            return sep.join(["GeminiCurie", "1", "2", "$1"]) + "\n"
        if args[:3] == ["list-panes", "-a", "-F"]:
            fmt = args[-1]
            if "#{pane_pid}" in fmt:
                return sep.join(["GeminiCurie", "11", "101"]) + "\n"
            return sep.join(["GeminiCurie", "11", "bash", ""]) + "\n"
        if args[:2] == ["list-clients", "-F"]:
            return ""
        return ""

    tree = (
        {
            101: "/bin/bash /tmp/gemini-runner.sh",
            202: "agy --input-format stream-json --output-format stream-json",
        },
        {101: [202]},
    )
    monkeypatch.setattr(server, "_tmux", fake_tmux)
    monkeypatch.setattr(provider_classification, "_process_tree_snapshot", lambda: tree)
    _reset_runtime_cache()

    sessions = server.tmux_state()

    assert sessions["GeminiCurie"]["cmd"] == "bash"
    assert sessions["GeminiCurie"]["_provider_runtime"]["headless"] is True
    assert server.classify(
        "GeminiCurie",
        sessions["GeminiCurie"]["cmd"],
        "",
        True,
        program="antigravity",
    ) == "agent"


def test_wrapped_agy_does_not_reclassify_another_provider():
    provider_classification._RUNTIME_CACHE.update(
        ts=1.0,
        names=("CodexCurie",),
        observations={
            "CodexCurie": {
                "provider": "gemini",
                "program": "antigravity",
                "runtime_command": "agy",
                "headless": True,
                "pid": 202,
            }
        },
    )

    assert server.classify(
        "CodexCurie", "bash", "", True, program="claude-code"
    ) == "finished"


def _mock_live_provider(monkeypatch, *, headless: bool) -> dict:
    observation = {
        "provider": "gemini",
        "program": "antigravity",
        "runtime_command": "agy",
        "headless": headless,
        "pid": 202,
    }
    monkeypatch.setattr(
        server,
        "tmux_state",
        lambda: {"GeminiCurie": {"_provider_runtime": observation}},
    )
    monkeypatch.setattr(server, "_agent_program", lambda _name: "antigravity")
    monkeypatch.setattr(
        server,
        "build_agents",
        lambda: [
            {
                "name": "GeminiCurie",
                "category": "agent",
                "running": True,
                "attached": False,
            }
        ],
    )
    monkeypatch.setattr(server, "_has_session", lambda _name: True)
    monkeypatch.setattr(
        provider_classification,
        "_fresh_provider_runtime_for_session",
        lambda _base, _session: dict(observation),
    )
    return observation


def test_headless_gemini_exit_interrupts_only_runtime_pid(monkeypatch):
    _mock_live_provider(monkeypatch, headless=True)

    kills: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        provider_classification.os,
        "kill",
        lambda pid, sig: kills.append((pid, sig)),
    )

    result = server.do_exit("GeminiCurie")

    assert result["ok"] is True
    assert "interrupt-sent" in result["actions"]
    assert "provider-headless:gemini" in result["actions"]
    assert kills == [(202, signal.SIGINT)]


def test_headless_exit_uses_fresh_runtime_pid_not_cached_pid(monkeypatch):
    observation = _mock_live_provider(monkeypatch, headless=True)
    assert observation["pid"] == 202
    fresh = dict(observation, pid=303)
    monkeypatch.setattr(
        provider_classification,
        "_fresh_provider_runtime_for_session",
        lambda _base, _session: fresh,
    )

    kills: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        provider_classification.os,
        "kill",
        lambda pid, sig: kills.append((pid, sig)),
    )

    result = server.do_exit("GeminiCurie")

    assert result["ok"] is True
    assert kills == [(303, signal.SIGINT)]


def test_provider_exit_refuses_when_fresh_runtime_disappears(monkeypatch):
    _mock_live_provider(monkeypatch, headless=True)
    monkeypatch.setattr(
        provider_classification,
        "_fresh_provider_runtime_for_session",
        lambda _base, _session: None,
    )

    kills: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(
        provider_classification.os,
        "kill",
        lambda pid, sig: kills.append((pid, sig)),
    )

    result = server.do_exit("GeminiCurie")

    assert result["ok"] is False
    assert "refresh and retry" in result["error"]
    assert kills == []


def test_wrapped_interactive_gemini_exit_types_slash_exit(monkeypatch):
    _mock_live_provider(monkeypatch, headless=False)
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(provider_classification.subprocess, "run", fake_run)

    result = server.do_exit("GeminiCurie")

    assert result["ok"] is True
    assert "exit-command-sent" in result["actions"]
    assert "provider-interactive:gemini" in result["actions"]
    assert calls == [
        ["tmux", "send-keys", "-t", "GeminiCurie", "-l", "/exit"],
        ["tmux", "send-keys", "-t", "GeminiCurie", "Enter"],
    ]


def test_agy_is_not_provider_exit_when_mail_identity_differs(monkeypatch):
    observation = {
        "provider": "gemini",
        "program": "antigravity",
        "runtime_command": "agy",
        "headless": True,
        "pid": 202,
    }
    monkeypatch.setattr(
        server,
        "tmux_state",
        lambda: {"ClaudeCurie": {"_provider_runtime": observation}},
    )
    monkeypatch.setattr(server, "_agent_program", lambda _name: "claude-code")

    assert (
        provider_classification._provider_runtime_for_session(server, "ClaudeCurie")
        is None
    )
