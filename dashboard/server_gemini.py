"""Fork-only Google Antigravity extensions for the dashboard server.

This module patches the existing dashboard implementation without duplicating
its ~5k-line control plane. `dashboard.server` installs these hooks at import
and then aliases itself to the original module so existing tests/monkeypatches
continue to target the real function globals.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import threading
from typing import Any

_GEMINI_DEFAULT_MODELS = (
    "gemini-3.8-flash-high",
    "gemini-3.8-flash",
)
_GEMINI_EFFORTS = ("low", "medium", "high")
_PATCH_LOCK = threading.Lock()


def _gemini_models() -> list[str]:
    values = [
        value.strip()
        for value in os.environ.get("AGENTSTACK_GEMINI_MODELS", "").split(",")
        if value.strip()
    ]
    return values or list(_GEMINI_DEFAULT_MODELS)


def install(base: Any) -> None:
    """Install Gemini catalog, spawn, render, and resume hooks on `base`."""
    original_spawn_names = base.spawn_names_payload
    original_do_spawn = base.do_spawn
    original_render = base._render_dashboard_index
    original_do_resume = base.do_resume

    def spawn_names_payload() -> dict:
        payload = original_spawn_names()
        providers = [
            provider
            for provider in payload.get("providers", [])
            if provider.get("id") != "gemini"
        ]
        models = _gemini_models()
        providers.append(
            {
                "id": "gemini",
                "label": "Gemini",
                "program": "antigravity",
                "models": models,
                "default_model": models[0],
                "efforts": list(_GEMINI_EFFORTS),
                "effort_default": "high",
            }
        )
        payload["providers"] = providers
        return payload

    def _render_dashboard_index(source: bytes, language: str = "", murmur: str = "") -> bytes:
        rendered = original_render(source, language, murmur)
        text = rendered.decode("utf-8")

        # Provider marks in deck + graph. The source UI intentionally remains
        # provider-generic; this fork injects only the Google additions.
        text = text.replace(
            "a.provider==='anthropic'||a.provider==='openai'",
            "a.provider==='anthropic'||a.provider==='openai'||a.provider==='google'",
        )
        text = text.replace(
            "openai:    256/260,   // ~0.98 (ほぼ正方形)\n};",
            "openai:    256/260,   // ~0.98 (ほぼ正方形)\n  google:    1,         // Gemini sparkle badge\n};",
        )
        text = text.replace(
            "if(/codex|gpt/.test(model))return 'openai';\n  return '';",
            "if(/codex|gpt/.test(model))return 'openai';\n  if(/gemini/.test(model))return 'google';\n  return '';",
        )

        # Gemini children require an explicit resource declaration. Add the
        # field to Advanced and force worktree isolation in the submitted
        # payload rather than silently opting out of reservations.
        isolation = """          <div class=\"spm-row full\">\n            <label class=\"spm-lab\">isolation</label>"""
        if 'id="spm-resources"' not in text and isolation in text:
            resource_row = """          <div class=\"spm-row full\">\n            <label class=\"spm-lab\" for=\"spm-resources\">resources (Gemini)</label>\n            <input type=\"text\" id=\"spm-resources\" placeholder=\"src/**,tests/**\" autocomplete=\"off\">\n            <div class=\"spm-hint\">Gemini launches require explicit comma-separated reservation paths.</div>\n          </div>\n"""
            text = text.replace(isolation, resource_row + isolation, 1)

        payload_marker = """    group:SPM('spm-group').value.trim()\n  };"""
        payload_replacement = """    group:SPM('spm-group').value.trim()\n  };\n  const resources=(SPM('spm-resources')&&SPM('spm-resources').value||'').trim();\n  if(resources)payload.resources=resources;\n  if(spmSelectedProvider==='gemini')payload.worktree=true;"""
        text = text.replace(payload_marker, payload_replacement, 1)

        # Keep the visible isolation checkbox consistent with the enforced
        # backend behavior when the Gemini provider tab is selected.
        provider_marker = """  spmSelectedEffort='';\n  SPM('spm-providers').querySelectorAll('.spm-provider-tab').forEach(btn=>{"""
        provider_replacement = """  spmSelectedEffort='';\n  if(SPM('spm-worktree')){\n    const geminiOnly=spmSelectedProvider==='gemini';\n    if(geminiOnly)SPM('spm-worktree').checked=true;\n    SPM('spm-worktree').disabled=geminiOnly;\n    SPM('spm-wt-base').classList.toggle('on',SPM('spm-worktree').checked);\n  }\n  SPM('spm-providers').querySelectorAll('.spm-provider-tab').forEach(btn=>{"""
        text = text.replace(provider_marker, provider_replacement, 1)
        return text.encode("utf-8")

    def do_spawn(payload: dict) -> dict:
        provider = str(payload.get("provider") or "claude").strip().lower()
        if provider != "gemini":
            return original_do_spawn(payload)

        models = _gemini_models()
        model = str(payload.get("model") or models[0]).strip()
        effort = str(payload.get("effort") or "high").strip().lower()
        resources = str(payload.get("resources") or "").strip()
        if model not in models:
            return {"ok": False, "error": f"model not allowed for provider gemini: {model}"}
        if effort not in _GEMINI_EFFORTS:
            return {"ok": False, "error": f"effort not allowed for provider gemini: {effort}"}
        if not resources:
            return {"ok": False, "error": "resources required for provider gemini"}
        if payload.get("standalone") is True:
            return {"ok": False, "error": "standalone not supported for provider gemini"}

        adapter = os.path.join(base.HOOKS_DIR, "spawn_gemini_preregistered.sh")
        if not os.path.exists(adapter):
            return {"ok": False, "error": f"Gemini spawn adapter missing: {adapter}"}

        task = str(payload.get("task") or "")
        os.makedirs(base.RUNTIME_DIR, exist_ok=True)
        fd, task_path = tempfile.mkstemp(
            prefix="gemini-dashboard-task-",
            suffix=".txt",
            dir=base.RUNTIME_DIR,
            text=True,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(task)

            translated = dict(payload)
            translated.update(
                provider="claude",
                model=model,
                effort="",
                worktree=True,
                async=False,
            )
            # `base.do_spawn` owns registration/name/contact policy/task mail and
            # token handoff. Temporarily present Gemini as one allowed launcher
            # entry; the adapter receives the full task via the 0600 file path.
            with _PATCH_LOCK:
                old_script = base.SPAWN_SCRIPT
                old_model = base._SPAWN_MODELS.get(model)
                old_effort = os.environ.get("AGENTSTACK_GEMINI_EFFORT")
                old_resources = os.environ.get("AGENTSTACK_GEMINI_RESOURCES")
                old_task = os.environ.get("AGENTSTACK_GEMINI_TASK_FILE")
                old_model_env = os.environ.get("AGENTSTACK_GEMINI_MODEL")
                try:
                    base.SPAWN_SCRIPT = adapter
                    base._SPAWN_MODELS[model] = ("antigravity", model)
                    os.environ["AGENTSTACK_GEMINI_EFFORT"] = effort
                    os.environ["AGENTSTACK_GEMINI_RESOURCES"] = resources
                    os.environ["AGENTSTACK_GEMINI_TASK_FILE"] = task_path
                    os.environ["AGENTSTACK_GEMINI_MODEL"] = model
                    result = original_do_spawn(translated)
                finally:
                    base.SPAWN_SCRIPT = old_script
                    if old_model is None:
                        base._SPAWN_MODELS.pop(model, None)
                    else:
                        base._SPAWN_MODELS[model] = old_model
                    for key, value in (
                        ("AGENTSTACK_GEMINI_EFFORT", old_effort),
                        ("AGENTSTACK_GEMINI_RESOURCES", old_resources),
                        ("AGENTSTACK_GEMINI_TASK_FILE", old_task),
                        ("AGENTSTACK_GEMINI_MODEL", old_model_env),
                    ):
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value

            if result.get("ok"):
                result["provider"] = "gemini"
                result["model"] = model
                result["effort"] = effort
                result["worktree"] = True
            return result
        finally:
            # Successful adapter launch consumes the file. Failed validation or
            # spawn paths may leave it behind, so cleanup is best-effort here.
            try:
                os.unlink(task_path)
            except FileNotFoundError:
                pass

    def do_resume(session: str) -> dict:
        if base._agent_program(session) == "antigravity":
            return {
                "ok": False,
                "error": (
                    "Antigravity resume is not yet bound to a durable conversation id; "
                    "refusing to fall through to Claude resume"
                ),
            }
        return original_do_resume(session)

    base.spawn_names_payload = spawn_names_payload
    base._render_dashboard_index = _render_dashboard_index
    base.do_spawn = do_spawn
    base.do_resume = do_resume
