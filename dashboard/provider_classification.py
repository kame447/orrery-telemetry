"""Capability-driven runtime classification and lifecycle for dashboard agents.

The legacy classifier already handles Claude/Codex activity heuristics. Provider
adapters can add runtimes below a shell wrapper (for example a delegated
Antigravity child runs ``agy`` inside a bash runner), so tmux's
``pane_current_command`` is not sufficient to decide liveness. This module
measures descendants of the active pane for registered provider runtimes and
keeps that provider-specific behavior out of the upstream-owned server.py.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any


_INTERPRETERS = {"node", "nodejs", "python", "python3", "bun", "deno"}
_RUNTIME_CACHE_LOCK = threading.Lock()
_RUNTIME_CACHE: dict[str, Any] = {"ts": 0.0, "names": (), "observations": {}}
_RUNTIME_CACHE_TTL = 0.75


def _process_tree_snapshot() -> tuple[dict[int, str], dict[int, list[int]]] | None:
    """Return one host process snapshot as (command_by_pid, children_by_ppid)."""
    if sys.platform == "win32":
        return None
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    commands: dict[int, str] = {}
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        commands[pid] = parts[2]
        children.setdefault(ppid, []).append(pid)
    return (commands, children) if commands else None


def _runtime_names(provider: Any) -> set[str]:
    names = {os.path.basename(str(provider.program)).lower()}
    names.update(
        os.path.basename(str(command)).lower()
        for command in provider.runtime_commands
        if str(command).strip()
    )
    return {name for name in names if name}


def _command_argv(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _command_matches_provider(command: str, provider: Any) -> tuple[bool, list[str]]:
    """Match the runtime executable or an interpreter's first script argument."""
    argv = _command_argv(command)
    if not argv:
        return False, argv
    names = _runtime_names(provider)
    executable = os.path.basename(argv[0]).lower()
    if executable in names:
        return True, argv

    interpreter = executable
    if interpreter.startswith("python"):
        interpreter = "python"
    if interpreter not in _INTERPRETERS:
        return False, argv
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        return os.path.basename(arg).lower() in names, argv
    return False, argv


def _is_headless_stream(argv: list[str]) -> bool:
    values: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--input-format", "--output-format"} and i + 1 < len(argv):
            values[arg] = argv[i + 1]
            i += 2
            continue
        i += 1
    return (
        values.get("--input-format") == "stream-json"
        and values.get("--output-format") == "stream-json"
    )


def _runtime_process_for_pane(
    pane_pid: object,
    process_tree: tuple[dict[int, str], dict[int, list[int]]] | None,
    registry: Any,
) -> dict[str, Any] | None:
    """Return provider runtime metadata for a pane root or its descendants."""
    try:
        root = int(pane_pid or 0)
    except (TypeError, ValueError):
        return None
    if root <= 0 or process_tree is None:
        return None
    commands, children = process_tree
    if root not in commands:
        return None

    stack = [root]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        command = commands.get(pid, "")
        for provider_id in registry.ids():
            provider = registry.require(provider_id)
            if not provider.capabilities.runtime:
                continue
            matches, argv = _command_matches_provider(command, provider)
            if matches:
                runtime_command = os.path.basename(argv[0]) if argv else provider.program
                if runtime_command.lower() in _INTERPRETERS and len(argv) > 1:
                    runtime_command = os.path.basename(
                        next((arg for arg in argv[1:] if not arg.startswith("-")), runtime_command)
                    )
                return {
                    "provider": provider.id,
                    "program": provider.program,
                    "runtime_command": runtime_command,
                    "command": command,
                    "headless": _is_headless_stream(argv),
                    "pid": pid,
                }
        stack.extend(children.get(pid, ()))
    return None


def _active_pane_pids(base: Any) -> dict[str, int]:
    sep = base.SEP
    fmt = sep.join(["#{session_name}", "#{window_active}#{pane_active}", "#{pane_pid}"])
    pids: dict[str, int] = {}
    for line in base._tmux(["list-panes", "-a", "-F", fmt]).splitlines():
        parts = line.split(sep, 2)
        if len(parts) != 3:
            continue
        name, flags, pane_pid = parts
        if flags != "11":
            continue
        try:
            pids[name] = int(pane_pid)
        except ValueError:
            continue
    return pids


def _runtime_observations(base: Any, session_names: set[str]) -> dict[str, dict[str, Any]]:
    names_key = tuple(sorted(session_names))
    now = time.monotonic()
    with _RUNTIME_CACHE_LOCK:
        if (
            names_key == _RUNTIME_CACHE["names"]
            and now - float(_RUNTIME_CACHE["ts"]) < _RUNTIME_CACHE_TTL
        ):
            return dict(_RUNTIME_CACHE["observations"])

    pids = _active_pane_pids(base)
    tree = _process_tree_snapshot() if pids else None
    observations: dict[str, dict[str, Any]] = {}
    for name in session_names:
        observation = _runtime_process_for_pane(
            pids.get(name), tree, base.PROVIDER_REGISTRY
        )
        if observation is not None:
            observations[name] = observation

    with _RUNTIME_CACHE_LOCK:
        _RUNTIME_CACHE.update(
            ts=now,
            names=names_key,
            observations=dict(observations),
        )
    return observations


def _cached_runtime_observation(name: str) -> dict[str, Any] | None:
    with _RUNTIME_CACHE_LOCK:
        observation = _RUNTIME_CACHE["observations"].get(name)
        return dict(observation) if isinstance(observation, dict) else None


def _provider_runtime_for_session(base: Any, session: str) -> dict[str, Any] | None:
    sessions = base.tmux_state()
    entry = sessions.get(session) or {}
    observation = entry.get("_provider_runtime")
    if not isinstance(observation, dict):
        return None
    program = base._agent_program(session)
    if not program or observation.get("program") != program:
        return None
    return observation


def _fresh_provider_runtime_for_session(base: Any, session: str) -> dict[str, Any] | None:
    """Measure the provider runtime again immediately before a destructive exit."""
    pane_pid = _active_pane_pids(base).get(session)
    if pane_pid is None:
        return None
    observation = _runtime_process_for_pane(
        pane_pid,
        _process_tree_snapshot(),
        base.PROVIDER_REGISTRY,
    )
    if not isinstance(observation, dict):
        return None
    program = base._agent_program(session)
    if not program or observation.get("program") != program:
        return None
    return observation


def install(base: Any) -> Any:
    """Teach the dashboard about registry runtimes and provider-aware exit."""
    if getattr(base, "_PROVIDER_CLASSIFICATION_INSTALLED", False):
        return base

    original_tmux_state = base.tmux_state
    original_classify = base.classify
    original_graph_payload = base.graph_payload
    original_exit = base.do_exit

    def tmux_state() -> dict:
        sessions = original_tmux_state()
        observations = _runtime_observations(base, set(sessions))
        for name, observation in observations.items():
            entry = sessions.get(name)
            if entry is not None:
                entry["_provider_runtime"] = observation
        return sessions

    def classify(
        name: str,
        cmd: str,
        title: str,
        in_mail: bool,
        program: str | None = None,
    ) -> str:
        result = original_classify(name, cmd, title, in_mail, program=program)
        if result != "finished" or not in_mail or not program:
            return result

        provider = base.PROVIDER_REGISTRY.by_program(program)
        if provider is None or not provider.capabilities.runtime:
            return result

        observation = _cached_runtime_observation(name)
        if (
            observation is not None
            and observation.get("program") == provider.program
        ):
            return "agent"

        live_commands = _runtime_names(provider)
        if os.path.basename(cmd or "").lower() in live_commands:
            return "agent"
        return result

    def graph_payload(days: float, show_all: bool) -> dict:
        # Ask core for the complete graph first so an old-but-running provider
        # child cannot be removed by the legacy Claude/Codex running filter.
        payload = original_graph_payload(days, True)
        nodes = payload.get("nodes")
        if not isinstance(nodes, list):
            return payload
        sessions = base.tmux_state()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            entry = sessions.get(str(node.get("name") or ""))
            observation = (entry or {}).get("_provider_runtime")
            if not isinstance(observation, dict):
                continue
            if observation.get("program") != (node.get("program") or ""):
                continue
            node["present"] = True
            node["running"] = True
            node["state"] = "run"

        if show_all:
            return payload

        mx = max(
            (int(node.get("last_active") or 0) for node in nodes),
            default=0,
        )
        window = days * 86400
        keep = {
            str(node.get("name") or "")
            for node in nodes
            if node.get("running")
            or (
                node.get("last_active")
                and (mx - int(node["last_active"])) <= window
            )
        }
        payload["nodes"] = [
            node for node in nodes if str(node.get("name") or "") in keep
        ]
        payload["edges"] = [
            edge
            for edge in payload.get("edges", [])
            if edge.get("source") in keep and edge.get("target") in keep
        ]
        payload["spawn"] = [
            edge
            for edge in payload.get("spawn", [])
            if edge.get("source") in keep and edge.get("target") in keep
        ]
        payload["shown"] = len(payload["nodes"])
        return payload

    def do_exit(session: str) -> dict:
        observation = _provider_runtime_for_session(base, session)
        if observation is None:
            return original_exit(session)

        if not base._valid(session):
            return {"ok": False, "error": "invalid session name"}
        if session.startswith("warm-") or session.startswith("pending-"):
            return {"ok": False, "error": "warmup/pending sessions are protected"}

        target = None
        try:
            for row in base.build_agents():
                if row["name"] == session:
                    target = row
                    break
        except Exception as exc:
            return {"ok": False, "error": f"failed to enumerate agents: {exc}"}
        if target is None:
            return {"ok": False, "error": f"agent '{session}' not found"}
        if target["category"] not in ("agent", "finished"):
            return {
                "ok": False,
                "error": (
                    f"agent '{session}' category={target['category']} "
                    "- only running/finished are exitable"
                ),
                "category": target.get("category"),
            }
        if not base._has_session(session):
            return {"ok": False, "error": f"tmux session '{session}' not found"}

        # The card/graph can be up to one cache interval old. Re-measure the
        # pane and process tree before sending /exit or a signal so a recycled
        # PID or changed foreground runtime is never acted on from stale state.
        fresh_observation = _fresh_provider_runtime_for_session(base, session)
        if fresh_observation is None:
            return {
                "ok": False,
                "error": "provider runtime changed before exit; refresh and retry",
            }
        observation = fresh_observation

        actions: list[str] = []
        if target.get("attached"):
            actions.append("warn-attached")

        if not observation.get("headless"):
            # The tmux pane may still report bash/zsh while the interactive
            # provider is the foreground child. Send its normal command to the
            # pty instead of letting the legacy shell-husk branch close the
            # wrapper shell.
            typed = subprocess.run(
                ["tmux", "send-keys", "-t", session, "-l", "/exit"],
                capture_output=True,
                text=True,
            )
            if typed.returncode != 0:
                return {
                    "ok": False,
                    "error": f"tmux send-keys failed: {typed.stderr.strip()}",
                }
            entered = subprocess.run(
                ["tmux", "send-keys", "-t", session, "Enter"],
                capture_output=True,
                text=True,
            )
            if entered.returncode != 0:
                return {
                    "ok": False,
                    "error": f"tmux send-keys Enter failed: {entered.stderr.strip()}",
                }
            actions.extend(
                [
                    "exit-command-sent",
                    f"provider-interactive:{observation['provider']}",
                ]
            )
            return {"ok": True, "session": session, "actions": actions}

        # Headless Antigravity consumes stream-json from a pipe, so typing
        # `/exit` into the tmux tty cannot reach its stdin. Signal only the
        # freshly measured provider runtime PID; keeping the bash runner alive
        # lets it report the interruption, release reservations, retire the
        # identity, and remove temporary credentials/config after the pipeline.
        try:
            runtime_pid = int(observation["pid"])
            os.kill(runtime_pid, signal.SIGINT)
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "provider runtime pid is unavailable"}
        except ProcessLookupError:
            return {
                "ok": False,
                "error": "provider runtime exited before interrupt; refresh and retry",
            }
        except PermissionError as exc:
            return {"ok": False, "error": f"cannot interrupt provider runtime: {exc}"}
        except OSError as exc:
            return {"ok": False, "error": f"provider runtime interrupt failed: {exc}"}

        actions.extend(
            [
                "interrupt-sent",
                f"provider-headless:{observation['provider']}",
            ]
        )
        return {"ok": True, "session": session, "actions": actions}

    base.tmux_state = tmux_state
    base.classify = classify
    base.graph_payload = graph_payload
    base.do_exit = do_exit
    base._PROVIDER_CLASSIFICATION_INSTALLED = True
    return base
