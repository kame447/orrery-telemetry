"""A truncated notice must tell the reader how to get the body.

fetch_inbox omits bodies unless include_bodies=true, so "Fetch inbox to read the
rest" alone sends a child to a call that returns no body.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HINT = "pass include_bodies=true"


def _delivery():
    spec = importlib.util.spec_from_file_location("runtime_delivery", ROOT / "bin/lib/runtime_delivery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_global_notice_names_include_bodies_for_preview_and_bodyless():
    fmt = _delivery().format_notification
    base = {"sender": {"name": "S"}, "subject": "T", "importance": "normal"}
    assert HINT in fmt({**base, "body_md": "x" * 2001})
    assert HINT in fmt(base)
    assert HINT not in fmt({**base, "body_md": "short"})  # complete body needs no fetch


def test_legacy_watcher_and_child_task_prompt_name_include_bodies():
    watcher = (ROOT / "hooks/watch_agent_mail_signals.sh").read_text(encoding="utf-8")
    assert watcher.count(HINT) == 2
    spawn = (ROOT / "hooks/spawn_child.sh").read_text(encoding="utf-8")
    assert "pass include_bodies=true when the schema accepts it" in spawn
