"""Install capability-driven provider behavior onto the legacy dashboard core.

The large dashboard server remains the stable control-plane implementation.
This module owns provider variation: catalog metadata, request validation,
adapter dispatch, UI capability hints, provider badges, and resume gating.
"""
from __future__ import annotations

import os
import re
import shlex
import tempfile
import threading
from typing import Any

try:
    from .providers.registry import ProviderRegistry, ProviderSpec
except ImportError:  # direct `python dashboard/server.py`
    from providers.registry import ProviderRegistry, ProviderSpec


_PATCH_LOCK = threading.Lock()


def _adapter_values(
    *, model: str, effort: str, resources: str, task_file: str
) -> dict[str, str]:
    return {
        "model": model,
        "effort": effort,
        "resources": resources,
        "task_file": task_file,
    }


def _format_adapter_env(
    provider: ProviderSpec,
    *, model: str,
    effort: str,
    resources: str,
    task_file: str,
) -> dict[str, str]:
    values = _adapter_values(
        model=model, effort=effort, resources=resources, task_file=task_file
    )
    result: dict[str, str] = {}
    for key, template in provider.adapter_env:
        try:
            result[key] = template.format_map(values)
        except KeyError as exc:
            raise ValueError(
                f"unknown adapter placeholder for provider {provider.id}: {exc.args[0]}"
            ) from exc
    return result


def _adapter_needs_task_file(provider: ProviderSpec) -> bool:
    return any("{task_file}" in template for _key, template in provider.adapter_env)


def _write_adapter_wrapper(
    base: Any,
    provider: ProviderSpec,
    adapter_script: str,
    adapter_env: dict[str, str],
    task_file: str,
) -> str:
    os.makedirs(base.RUNTIME_DIR, mode=0o700, exist_ok=True)
    fd, wrapper = tempfile.mkstemp(
        prefix=f"provider-{provider.id}-",
        suffix=".sh",
        dir=base.RUNTIME_DIR,
        text=True,
    )
    cleanup_paths = [wrapper]
    if task_file:
        cleanup_paths.append(task_file)
    cleanup = " ".join(shlex.quote(path) for path in cleanup_paths)
    env_lines = "".join(
        f"export {key}={shlex.quote(value)}\n"
        for key, value in adapter_env.items()
    )
    prefix = " ".join(
        shlex.quote(value) for value in [adapter_script, *provider.launch_args]
    )
    body = (
        "#!/bin/bash\n"
        "set -u\n"
        + env_lines
        + f"{prefix} \"$@\"\n"
        + "status=$?\n"
        + f"rm -f -- {cleanup}\n"
        + "exit \"$status\"\n"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.chmod(wrapper, 0o700)
    return wrapper


def _install_catalog(base: Any) -> None:
    original = base.spawn_names_payload

    def spawn_names_payload() -> dict:
        payload = original()
        registry: ProviderRegistry = base.PROVIDER_REGISTRY
        payload["providers"] = registry.catalog()
        # Backward-compatible top-level model keys for old dashboard clients.
        if "claude" in registry.ids():
            claude = registry.require("claude")
            payload["models"] = list(claude.resolved_models())
            payload["default_model"] = claude.resolved_default_model()
        return payload

    base.spawn_names_payload = spawn_names_payload


def _install_provider_classification(base: Any) -> None:
    original = base._provider_of

    def _provider_of(raw: str | None) -> str:
        known = original(raw)
        if known:
            return known
        value = (raw or "").strip().lower()
        if not value:
            return ""
        registry: ProviderRegistry = base.PROVIDER_REGISTRY
        for provider_id in registry.ids():
            provider = registry.require(provider_id)
            if any(value == model.lower() for model in provider.resolved_models()):
                return provider.provider_key
        return ""

    base._provider_of = _provider_of


def _install_spawn(base: Any) -> None:
    original = base.do_spawn

    def do_spawn(payload: dict) -> dict:
        if "standalone" in payload and not isinstance(payload["standalone"], bool):
            return {"ok": False, "error": "standalone must be boolean"}

        registry: ProviderRegistry = base.PROVIDER_REGISTRY
        provider_id = str(payload.get("provider") or "claude").strip().lower()
        try:
            provider = registry.require(provider_id)
            validated = registry.validate_request(
                provider_id,
                str(payload.get("model") or ""),
                str(payload.get("effort") or ""),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        standalone = payload.get("standalone", False)
        if standalone and not provider.capabilities.standalone:
            return {
                "ok": False,
                "error": f"standalone not supported for provider {provider.id}",
            }
        resources = str(payload.get("resources") or "").strip()
        if provider.capabilities.resources_required and not resources:
            return {
                "ok": False,
                "error": f"resources required for provider {provider.id}",
            }

        normalized = dict(payload)
        normalized.update(
            provider=provider.id,
            model=validated["model"],
            effort=validated["effort"],
        )
        if provider.capabilities.worktree_required:
            normalized["worktree"] = True

        if provider.dispatch == "native":
            # Adapter launches temporarily patch legacy globals below. Native
            # launches must participate in the same lock or they can observe an
            # adapter's SPAWN_SCRIPT/_SPAWN_MODELS values and launch the wrong
            # provider under concurrency.
            with _PATCH_LOCK:
                return original(normalized)

        adapter_script = (
            os.path.join(base.HOOKS_DIR, provider.adapter_script)
            if provider.adapter_script
            else base.SPAWN_SCRIPT
        )
        if not os.path.exists(adapter_script):
            return {
                "ok": False,
                "error": f"spawn adapter missing for provider {provider.id}: {adapter_script}",
            }

        task_file = ""
        wrapper = ""
        handed_off = False
        try:
            if _adapter_needs_task_file(provider):
                os.makedirs(base.RUNTIME_DIR, mode=0o700, exist_ok=True)
                fd, task_file = tempfile.mkstemp(
                    prefix=f"provider-{provider.id}-task-",
                    suffix=".txt",
                    dir=base.RUNTIME_DIR,
                    text=True,
                )
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(payload.get("task") or ""))

            try:
                adapter_env = _format_adapter_env(
                    provider,
                    model=validated["model"],
                    effort=validated["effort"],
                    resources=resources,
                    task_file=task_file,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            wrapper = _write_adapter_wrapper(
                base, provider, adapter_script, adapter_env, task_file
            )

            # Reuse the mature registration/token/contact/readiness pipeline.
            # The generic adapter is represented temporarily through the legacy
            # Claude slot only because server_core still owns that common path.
            translated = dict(normalized)
            translated.update(provider="claude", effort="")
            with _PATCH_LOCK:
                old_script = base.SPAWN_SCRIPT
                old_model = base._SPAWN_MODELS.get(validated["model"])
                try:
                    base.SPAWN_SCRIPT = wrapper
                    base._SPAWN_MODELS[validated["model"]] = (
                        provider.program,
                        validated["model"],
                    )
                    result = original(translated)
                finally:
                    base.SPAWN_SCRIPT = old_script
                    if old_model is None:
                        base._SPAWN_MODELS.pop(validated["model"], None)
                    else:
                        base._SPAWN_MODELS[validated["model"]] = old_model

            if result.get("ok"):
                result.update(
                    provider=provider.id,
                    model=validated["model"],
                    effort=validated["effort"] or None,
                    worktree=bool(normalized.get("worktree")),
                )
                # Popen has already received the wrapper path. The wrapper now
                # owns its own/task-file cleanup, including asynchronous settle.
                handed_off = True
            return result
        finally:
            if not handed_off:
                for path in (wrapper, task_file):
                    if path:
                        try:
                            os.unlink(path)
                        except FileNotFoundError:
                            pass

    base.do_spawn = do_spawn


def _install_resume_guard(base: Any) -> None:
    original = base.do_resume

    def do_resume(session: str) -> dict:
        program = base._agent_program(session)
        provider = base.PROVIDER_REGISTRY.by_program(program)
        if provider is not None and not provider.capabilities.resume:
            return {
                "ok": False,
                "error": (
                    f"resume not supported for provider {provider.id}; "
                    "refusing to fall through to another provider's resume path"
                ),
            }
        return original(session)

    base.do_resume = do_resume


def _inject_ui_capabilities(text: str, registry: ProviderRegistry) -> str:
    # Deck provider logos: use the provider metadata table rather than a fixed
    # Anthropic/OpenAI conditional.
    text = text.replace(
        "a.provider==='anthropic'||a.provider==='openai'",
        "a.provider&&_PROVIDER_ASPECT[a.provider]",
    )

    # Extend the existing provider-aspect table from registry metadata. The
    # logo asset convention is /assets/<provider_key>.svg.
    match = re.search(r"const _PROVIDER_ASPECT = \{(?P<body>.*?)\n\};", text, re.S)
    if match:
        body = match.group("body")
        additions = []
        for provider_id in registry.ids():
            provider = registry.require(provider_id)
            key = provider.provider_key
            if re.search(rf"\b{re.escape(key)}\s*:", body):
                continue
            additions.append(f"  {key}: {provider.logo_aspect:g},")
        if additions:
            replacement = (
                "const _PROVIDER_ASPECT = {"
                + body
                + "\n"
                + "\n".join(additions)
                + "\n};"
            )
            text = text[: match.start()] + replacement + text[match.end() :]

    # One generic reservation field. Visibility and forced worktree state are
    # driven entirely by provider.capabilities from the catalog.
    isolation = """          <div class=\"spm-row full\">\n            <label class=\"spm-lab\">isolation</label>"""
    if 'id="spm-resources-row"' not in text and isolation in text:
        resource_row = """          <div class=\"spm-row full\" id=\"spm-resources-row\" style=\"display:none\">\n            <label class=\"spm-lab\" for=\"spm-resources\">resources</label>\n            <input type=\"text\" id=\"spm-resources\" placeholder=\"src/**,tests/**\" autocomplete=\"off\">\n            <div class=\"spm-hint\">Comma-separated paths reserved for providers that require resource isolation.</div>\n          </div>\n"""
        text = text.replace(isolation, resource_row + isolation, 1)

    provider_marker = """  spmSelectedEffort='';\n  SPM('spm-providers').querySelectorAll('.spm-provider-tab').forEach(btn=>{"""
    if (
        "const providerCaps=provider&&provider.capabilities||{};" not in text
        and provider_marker in text
    ):
        provider_replacement = """  spmSelectedEffort='';\n  const providerCaps=provider&&provider.capabilities||{};\n  if(SPM('spm-resources-row'))SPM('spm-resources-row').style.display=providerCaps.resources_required?'grid':'none';\n  if(SPM('spm-resources'))SPM('spm-resources').oninput=updateSpawnButton;\n  if(SPM('spm-worktree')){\n    if(providerCaps.worktree_required)SPM('spm-worktree').checked=true;\n    SPM('spm-worktree').disabled=!!providerCaps.worktree_required;\n    SPM('spm-wt-base').classList.toggle('on',SPM('spm-worktree').checked);\n  }\n  SPM('spm-providers').querySelectorAll('.spm-provider-tab').forEach(btn=>{"""
        text = text.replace(provider_marker, provider_replacement, 1)

    payload_marker = """    group:SPM('spm-group').value.trim()\n  };"""
    if "payload.resources=resources" not in text and payload_marker in text:
        payload_replacement = """    group:SPM('spm-group').value.trim()\n  };\n  const resources=(SPM('spm-resources')&&SPM('spm-resources').value||'').trim();\n  if(resources)payload.resources=resources;"""
        text = text.replace(payload_marker, payload_replacement, 1)

    button_marker = """  const identityReady=!spmSelectedName||spmIdentityState==='verified';\n  button.disabled=spmBusy||!spmReady||!identityReady;"""
    if "const resourcesReady=!providerCaps.resources_required" not in text and button_marker in text:
        button_replacement = """  const identityReady=!spmSelectedName||spmIdentityState==='verified';\n  const selectedProvider=spmProviders.find(item=>item.id===spmSelectedProvider);\n  const providerCaps=selectedProvider&&selectedProvider.capabilities||{};\n  const resourcesReady=!providerCaps.resources_required||!!(SPM('spm-resources')&&SPM('spm-resources').value.trim());\n  button.disabled=spmBusy||!spmReady||!identityReady||!resourcesReady;"""
        text = text.replace(button_marker, button_replacement, 1)
    return text


def _install_render(base: Any) -> None:
    original = base._render_dashboard_index

    def _render_dashboard_index(
        source: bytes, language: str = "", murmur: str = ""
    ) -> bytes:
        rendered = original(source, language, murmur)
        text = rendered.decode("utf-8")
        return _inject_ui_capabilities(text, base.PROVIDER_REGISTRY).encode("utf-8")

    base._render_dashboard_index = _render_dashboard_index


def install(base: Any, registry: ProviderRegistry) -> Any:
    """Install provider behavior once and return the patched dashboard module."""
    base.PROVIDER_REGISTRY = registry
    if getattr(base, "_PROVIDER_RUNTIME_INSTALLED", False):
        return base
    _install_catalog(base)
    _install_provider_classification(base)
    _install_spawn(base)
    _install_resume_guard(base)
    _install_render(base)
    base._PROVIDER_RUNTIME_INSTALLED = True
    return base
