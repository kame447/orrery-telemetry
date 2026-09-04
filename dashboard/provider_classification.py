"""Capability-driven runtime classification for dashboard agents.

The legacy classifier already handles Claude/Codex activity heuristics. This
adapter resolves the fallback case for registered providers without treating a
retained shell as a live agent after the provider CLI has exited.
"""
from __future__ import annotations

from typing import Any


def install(base: Any) -> Any:
    """Teach the dashboard classifier about registry runtime commands."""
    if getattr(base, "_PROVIDER_CLASSIFICATION_INSTALLED", False):
        return base

    original = base.classify

    def classify(
        name: str,
        cmd: str,
        title: str,
        in_mail: bool,
        program: str | None = None,
    ) -> str:
        result = original(name, cmd, title, in_mail, program=program)
        if result != "finished" or not in_mail or not program:
            return result

        provider = base.PROVIDER_REGISTRY.by_program(program)
        if provider is None or not provider.capabilities.runtime:
            return result

        live_commands = {provider.program, *provider.runtime_commands}
        if cmd in live_commands:
            return "agent"
        return result

    base.classify = classify
    base._PROVIDER_CLASSIFICATION_INSTALLED = True
    return base
