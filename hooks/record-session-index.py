#!/usr/bin/env python3
"""record-session-index.py

Called from mark-agent-registered.sh (PostToolUse on register_agent).

Records a precise, unambiguous mapping so the agent-dashboard can resume the
EXACT Claude session belonging to an ORRERY Mail row — instead of guessing via
name self-reference scoring + an mtime activity window (which mis-fires when
`last_active_ts` is stuck at inception, or when a name is reused across
projects; see logs/ in the dashboard project).

Key = ORRERY Mail `id` (global PRIMARY KEY → unique per session). For each
register_agent we write:

    ~/.agentstack/runtime/session_index/<agent_id>.json
        {agent_id, agent_name, session_id, transcript_path, cwd, ts}

Idempotent re-registration / resume keeps the same id and session_id, so we
simply overwrite (keeps the entry fresh). The dashboard reads this first and
only falls back to the heuristic for old sessions registered before this hook
existed.

Reads the PostToolUse hook payload (JSON) on stdin. Never raises — a failure
here must not disturb registration.
"""
import json
import os
import pathlib
import stat
import subprocess
import sys
import time


def _normalize_project_key(value):
    if not isinstance(value, str) or not value:
        return ""
    if os.path.isabs(value) or os.path.isdir(value):
        return os.path.realpath(value)
    return value


def _extract_id(v):
    """Pull ORRERY Mail numeric id out of the register_agent tool_response,
    which may be a JSON string, a dict, or the MCP content-block wrapper."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return None
    if isinstance(v, dict):
        if isinstance(v.get("id"), int):
            return v["id"]
        content = v.get("content")
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    got = _extract_id(blk.get("text", ""))
                    if got is not None:
                        return got
    return None


def _extract_name(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return ""
    if isinstance(v, dict):
        if v.get("name"):
            return v["name"]
        content = v.get("content")
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    got = _extract_name(blk.get("text", ""))
                    if got:
                        return got
    return ""


# Outcomes the caller acts on. mark-agent-registered.sh creates the session's
# registration flag only for EXIT_BOUND: a flag that outlives a refused or
# failed binding tells the guards "registered" while leaving them unable to say
# who this is.
EXIT_BOUND = 0
EXIT_NOT_APPLICABLE = 3
EXIT_DELEGATED = 4
EXIT_CALLER_UNRESOLVED = 5
EXIT_WRITE_FAILED = 6
EXIT_PROJECT_MISMATCH = 7

# Sources resolve-agent-name.sh reports that identify one agent. Anything else
# (identity-conflict, placeholder-env, unconfirmed-metafile) is a caller whose
# identity was refused, not an absent one.
_SOURCES_THAT_MAY_BIND = {"none", "env", "tmux-session", "metafile+tmux-session", "session-index"}


def _repository_key(path_value):
    if not isinstance(path_value, str) or not os.path.isdir(path_value):
        return ""
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    try:
        common = subprocess.check_output(
            ["git", "-C", path_value, "rev-parse", "--git-common-dir"],
            text=True,
            stderr=subprocess.DEVNULL,
            env=env,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    common_path = pathlib.Path(common)
    if not common_path.is_absolute():
        common_path = pathlib.Path(path_value) / common_path
    common_path = pathlib.Path(os.path.realpath(common_path))
    return str(common_path.parent if common_path.name == ".git" else common_path)


def _binding_matches_context(record, context):
    schema = record.get("schema_version")
    repository = context.get("repository_key")
    work_dir = context.get("work_dir")
    if schema == 3:
        if repository:
            return record.get("repository_key") == repository
        return (
            record.get("repository_key") is None
            and record.get("work_dir") == work_dir
        )
    if schema == 2:
        # v2 had no repository field. Its namespace must still equal the
        # derived namespace, while cwd independently corroborates the actual
        # repository (or exact non-Git workspace).
        if _normalize_project_key(record.get("project_key")) != _normalize_project_key(
            context.get("project_key")
        ):
            return False
        if repository:
            return _repository_key(record.get("cwd")) == repository
        legacy_cwd = record.get("cwd")
        return (
            isinstance(legacy_cwd, str)
            and os.path.isdir(legacy_cwd)
            and os.path.realpath(legacy_cwd) == work_dir
        )
    return False


def _strong_owner_contradicts(record, context, runtime_dir):
    name = record.get("agent_name")
    if not isinstance(name, str) or not name:
        return True
    safe_name = "".join(
        char if char.isascii() and (char.isalnum() or char in "_.-") else "_"
        for char in name
    )
    owner_path = pathlib.Path(runtime_dir) / f"agent_owner_{safe_name}.json"
    if not owner_path.exists() and not owner_path.is_symlink():
        return False
    if owner_path.is_symlink():
        return True
    try:
        if stat.S_IMODE(owner_path.stat().st_mode) & 0o077:
            return True
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(owner, dict) or owner.get("schema") != 1:
        return True
    if owner.get("agent_name") != name:
        return True
    if _normalize_project_key(owner.get("project_key")) != _normalize_project_key(
        record.get("project_key")
    ):
        return True
    repository = context.get("repository_key")
    if repository:
        return owner.get("repository_key") != repository
    root = owner.get("non_git_root")
    try:
        pathlib.Path(context.get("work_dir")).relative_to(pathlib.Path(root))
    except (TypeError, ValueError):
        return True
    return owner.get("repository_key") is not None


def _bindings_for(out_dir, session_id, context):
    """Names already bound to this session by an authoritative record."""
    names = set()
    try:
        entries = os.listdir(out_dir)
    except OSError:
        return names
    for entry in entries:
        if not entry.endswith(".json"):
            continue
        path = os.path.join(out_dir, entry)
        if os.path.islink(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("session_id") != session_id:
            continue
        if record.get("binding_kind") != "self":
            continue
        if not _binding_matches_context(record, context):
            continue
        name = record.get("agent_name")
        if isinstance(name, str) and name:
            if record.get("schema_version") == 2 and _strong_owner_contradicts(
                record, context, os.path.dirname(out_dir)
            ):
                continue
            names.add(name)
    return names


def main():
    try:
        d = json.loads(sys.stdin.read())
    except Exception:
        return EXIT_NOT_APPLICABLE

    session_id = d.get("session_id") or ""
    transcript_path = d.get("transcript_path") or ""
    cwd = d.get("cwd") or ""

    resp = d.get("tool_response")
    if resp is None:
        resp = d.get("tool_result")
    agent_id = _extract_id(resp)
    tool_input = d.get("tool_input") or {}
    agent_name = _extract_name(resp) or tool_input.get("name", "")

    # The caller has already re-resolved this tuple from the hook's actual cwd.
    # The raw tool_input is never authority: generated instructions and resumed
    # clients can carry an installed project's stale namespace.
    try:
        context = json.loads(os.environ.get("AGENTSTACK_VALIDATED_CONTEXT_JSON", ""))
    except Exception:
        return EXIT_PROJECT_MISMATCH
    if not isinstance(context, dict):
        return EXIT_PROJECT_MISMATCH
    project_key = context.get("project_key")
    repository_key = context.get("repository_key")
    context_work_dir = context.get("work_dir")
    worktree_root = context.get("worktree_root")
    protected_roots = context.get("protected_roots")
    supplied_project = tool_input.get("project_key") or ""
    if (
        not isinstance(project_key, str)
        or not project_key
        or _normalize_project_key(supplied_project) != _normalize_project_key(project_key)
        or not isinstance(context_work_dir, str)
        or not context_work_dir
        or not isinstance(cwd, str)
        or not os.path.isdir(cwd)
        or os.path.realpath(cwd) != context_work_dir
        or (repository_key is not None and not isinstance(repository_key, str))
        or (worktree_root is not None and not isinstance(worktree_root, str))
        or protected_roots != [worktree_root or context_work_dir]
    ):
        return EXIT_PROJECT_MISMATCH

    # Who called register_agent, as mark-agent-registered.sh resolved it. Both
    # halves are required: the name alone cannot distinguish "nobody claims this
    # session" from "the claim was refused", and a refused claim must not be
    # read as an anonymous self-registration.
    registered_by = os.environ.get("AGENTSTACK_REGISTERING_AGENT", "")
    # Absent means the writer was invoked directly rather than through
    # mark-agent-registered.sh: an anonymous caller, which may claim only a
    # session no other identity has claimed.
    caller_source = os.environ.get("AGENTSTACK_REGISTERING_SOURCE", "") or "none"

    # Need at least an id (the unique key) and a session_id to be useful.
    if agent_id is None or not session_id:
        return EXIT_NOT_APPLICABLE

    if caller_source not in _SOURCES_THAT_MAY_BIND:
        # An unresolved, conflicting or unconfirmed caller cannot be shown to be
        # this agent. Adding a third name to a session that already has two is
        # how a conflict becomes permanent.
        return EXIT_CALLER_UNRESOLVED

    # A registration made on somebody else's behalf is not a binding for this
    # session, and writing it anyway leaves a record every reader has to know
    # to distrust -- the dashboard used one to show a parent's transcript on a
    # child's card. Not writing it is the version that cannot be misread.
    if registered_by and agent_name and registered_by != agent_name:
        return EXIT_DELEGATED
    if caller_source != "none" and registered_by != agent_name:
        return EXIT_DELEGATED

    runtime_dir = os.path.expanduser(
        os.environ.get("AGENTSTACK_RUNTIME_DIR", "~/.agentstack/runtime")
    )
    out_dir = os.path.join(runtime_dir, "session_index")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        return EXIT_WRITE_FAILED

    # An anonymous caller may only claim a session nobody else has claimed.
    if caller_source == "none":
        for existing in _bindings_for(out_dir, session_id, context):
            if existing != agent_name:
                return EXIT_CALLER_UNRESOLVED

    record = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": context_work_dir,
        "project_key": project_key,
        "repository_key": repository_key,
        "work_dir": context_work_dir,
        "worktree_root": worktree_root,
        "protected_roots": protected_roots,
        "registered_by": registered_by,
        # Readers that treat this file as authority check these two, so a record
        # written by an older version is ignored rather than half-trusted.
        "schema_version": 3,
        "binding_kind": "self",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = os.path.join(out_dir, f"{agent_id}.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        # Swallowing this used to be harmless -- the index was a convenience for
        # the dashboard. It is now the identity the guards check, so a failure
        # has to reach the caller instead of leaving a flag with no binding.
        return EXIT_WRITE_FAILED
    return EXIT_BOUND


if __name__ == "__main__":
    try:
        raise SystemExit(main() or EXIT_BOUND)
    except SystemExit:
        raise
    except Exception:
        # A crash is not a binding either.
        raise SystemExit(EXIT_WRITE_FAILED)
