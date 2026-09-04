"""Launch-provider registry shared by dashboard catalog and dispatch.

Provider-specific facts belong here. The dashboard core asks this registry
what a provider can do instead of growing ``if provider == ...`` branches each
time another CLI is added.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Iterable


@dataclass(frozen=True)
class ProviderCapabilities:
    effort: bool = False
    mcp: bool = True
    resume: bool = True
    runtime: bool = True
    transcript: bool = True
    standalone: bool = True
    worktree_required: bool = False
    resources_required: bool = False


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    program: str
    models: tuple[str, ...]
    default_model: str
    capabilities: ProviderCapabilities
    provider_key: str
    efforts: tuple[str, ...] = ()
    effort_default: str = ""
    # ``native`` delegates to the legacy Claude/Codex branch while it is being
    # migrated. ``adapter`` uses the generic adapter path. This is provider
    # metadata rather than a provider-name conditional in the dashboard core.
    dispatch: str = "adapter"
    adapter_script: str = ""
    launch_args: tuple[str, ...] = ()
    # Values may reference {effort}, {resources}, and {task_file}.
    adapter_env: tuple[tuple[str, str], ...] = ()
    logo_aspect: float = 1.0

    def __post_init__(self) -> None:
        if not self.id or not self.program or not self.models:
            raise ValueError("provider id, program and models are required")
        if self.default_model not in self.models:
            raise ValueError(f"default model is not registered for provider {self.id}")
        if self.capabilities.effort:
            if not self.efforts:
                raise ValueError(f"effort levels are required for provider {self.id}")
            if self.effort_default not in self.efforts:
                raise ValueError(f"effort default is invalid for provider {self.id}")
        elif self.efforts or self.effort_default:
            raise ValueError(f"effort metadata is not allowed for provider {self.id}")
        if self.dispatch not in {"native", "adapter"}:
            raise ValueError(f"unknown dispatch mode for provider {self.id}: {self.dispatch}")
        for key, _value in self.adapter_env:
            if not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
                raise ValueError(f"invalid adapter environment key for {self.id}: {key}")

    def catalog_item(self) -> dict:
        item = {
            "id": self.id,
            "label": self.label,
            "program": self.program,
            "models": list(self.models),
            "default_model": self.default_model,
            "efforts": list(self.efforts) if self.capabilities.effort else None,
            "capabilities": asdict(self.capabilities),
            "provider_key": self.provider_key,
        }
        if self.capabilities.effort:
            item["effort_default"] = self.effort_default
        return item


class ProviderRegistry:
    def __init__(self, providers: Iterable[ProviderSpec] = ()) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderSpec) -> None:
        if provider.id in self._providers:
            raise ValueError(f"provider already registered: {provider.id}")
        self._providers[provider.id] = provider

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def require(self, provider_id: str) -> ProviderSpec:
        key = (provider_id or "").strip().lower()
        try:
            return self._providers[key]
        except KeyError as exc:
            raise ValueError(f"provider not allowed: {key or provider_id}") from exc

    def by_program(self, program: str) -> ProviderSpec | None:
        for provider in self._providers.values():
            if provider.program == program:
                return provider
        return None

    def catalog(self) -> list[dict]:
        return [provider.catalog_item() for provider in self._providers.values()]

    def validate_request(
        self, provider_id: str, model: str = "", effort: str = ""
    ) -> dict:
        provider = self.require(provider_id)
        selected_model = (model or provider.default_model).strip()
        if selected_model not in provider.models:
            raise ValueError(
                f"model not allowed for provider {provider.id}: {selected_model}"
            )

        selected_effort = (effort or "").strip().lower()
        if provider.capabilities.effort:
            selected_effort = selected_effort or provider.effort_default
            if selected_effort not in provider.efforts:
                raise ValueError(
                    f"effort not allowed for provider {provider.id}: {selected_effort}"
                )
        elif selected_effort:
            raise ValueError(f"effort not supported for provider: {provider.id}")

        return {
            "provider": provider.id,
            "model": selected_model,
            "effort": selected_effort,
            "worktree_required": provider.capabilities.worktree_required,
            "resources_required": provider.capabilities.resources_required,
        }


def _env_models(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(
        value.strip()
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    )
    return values or defaults


def default_provider_registry() -> ProviderRegistry:
    codex_models = _env_models(
        "AGENTSTACK_CODEX_MODELS",
        ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
    )
    gemini_models = _env_models(
        "AGENTSTACK_GEMINI_MODELS",
        ("gemini-3.8-flash-high", "gemini-3.8-flash-medium"),
    )
    return ProviderRegistry(
        [
            ProviderSpec(
                id="claude",
                label="Claude",
                program="claude-code",
                models=(
                    "claude-sonnet-5",
                    "claude-opus-5",
                    "claude-haiku-4-5-20251001",
                ),
                default_model="claude-sonnet-5",
                capabilities=ProviderCapabilities(effort=False),
                provider_key="anthropic",
                dispatch="native",
            ),
            ProviderSpec(
                id="codex",
                label="Codex",
                program="codex-cli",
                models=codex_models,
                default_model=codex_models[0],
                capabilities=ProviderCapabilities(effort=True),
                provider_key="openai",
                efforts=("low", "medium", "high", "xhigh"),
                effort_default="xhigh",
                dispatch="native",
                launch_args=("--codex",),
                logo_aspect=256 / 260,
            ),
            ProviderSpec(
                id="gemini",
                label="Gemini",
                program="antigravity",
                models=gemini_models,
                default_model=gemini_models[0],
                capabilities=ProviderCapabilities(
                    effort=True,
                    mcp=True,
                    resume=False,
                    runtime=True,
                    transcript=False,
                    standalone=False,
                    worktree_required=True,
                    resources_required=True,
                ),
                provider_key="google",
                efforts=("low", "medium", "high"),
                effort_default="high",
                dispatch="adapter",
                adapter_script="spawn_gemini_preregistered.sh",
                adapter_env=(
                    ("AGENTSTACK_GEMINI_EFFORT", "{effort}"),
                    ("AGENTSTACK_GEMINI_RESOURCES", "{resources}"),
                    ("AGENTSTACK_GEMINI_TASK_FILE", "{task_file}"),
                    ("AGENTSTACK_GEMINI_MODEL", "{model}"),
                ),
            ),
        ]
    )
