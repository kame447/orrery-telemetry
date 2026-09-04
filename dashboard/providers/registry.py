"""Launch-provider registry shared by dashboard catalog and dispatch.

Provider-specific facts belong in declarative manifests. The dashboard core asks
this registry what a provider can do instead of growing provider-name branches.
Claude and Codex remain built-ins because they are part of the upstream core;
optional providers are discovered from ``provider_specs/*.json``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterable


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
    dispatch: str = "adapter"
    adapter_script: str = ""
    launch_args: tuple[str, ...] = ()
    adapter_env: tuple[tuple[str, str], ...] = ()
    logo_aspect: float = 1.0
    models_env: str = ""
    required_paths: tuple[str, ...] = ()
    runtime_commands: tuple[str, ...] = ()

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
        if self.dispatch == "adapter" and not self.adapter_script:
            raise ValueError(f"adapter script is required for provider {self.id}")
        for key, _value in self.adapter_env:
            if not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
                raise ValueError(f"invalid adapter environment key for {self.id}: {key}")
        for relative in self.required_paths:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"required provider path must be install-relative: {relative}")
        if any(not command.strip() for command in self.runtime_commands):
            raise ValueError(f"runtime commands must be non-empty for provider {self.id}")

    def resolved_models(self) -> tuple[str, ...]:
        if not self.models_env:
            return self.models
        values = tuple(
            value.strip()
            for value in os.environ.get(self.models_env, "").split(",")
            if value.strip()
        )
        return values or self.models

    def resolved_default_model(self) -> str:
        models = self.resolved_models()
        if self.models_env and models != self.models:
            return models[0]
        return self.default_model

    def is_available(self, install_root: str | os.PathLike[str]) -> bool:
        root = Path(install_root)
        return all((root / relative).is_file() for relative in self.required_paths)

    def catalog_item(self) -> dict:
        item = {
            "id": self.id,
            "label": self.label,
            "program": self.program,
            "models": list(self.resolved_models()),
            "default_model": self.resolved_default_model(),
            "efforts": list(self.efforts) if self.capabilities.effort else None,
        }
        if self.capabilities.effort:
            item["effort_default"] = self.effort_default
        # Keep the historical Claude/Codex JSON shape stable. Extra providers
        # carry capability metadata so the GUI can adapt without name checks.
        if self.dispatch != "native" or self.provider_key not in {"anthropic", "openai"}:
            item["capabilities"] = asdict(self.capabilities)
            item["provider_key"] = self.provider_key
        return item


class ProviderRegistry:
    def __init__(self, providers: Iterable[ProviderSpec] = ()) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        self._programs: dict[str, str] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderSpec) -> None:
        if provider.id in self._providers:
            raise ValueError(f"provider already registered: {provider.id}")
        owner = self._programs.get(provider.program)
        if owner is not None:
            raise ValueError(f"provider program already registered: {provider.program}")
        self._providers[provider.id] = provider
        self._programs[provider.program] = provider.id

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def require(self, provider_id: str) -> ProviderSpec:
        key = (provider_id or "").strip().lower()
        try:
            return self._providers[key]
        except KeyError as exc:
            raise ValueError(f"provider not allowed: {key or provider_id}") from exc

    def by_program(self, program: str) -> ProviderSpec | None:
        provider_id = self._programs.get(program)
        return self._providers.get(provider_id) if provider_id is not None else None

    def catalog(self) -> list[dict]:
        return [provider.catalog_item() for provider in self._providers.values()]

    def validate_request(
        self, provider_id: str, model: str = "", effort: str = ""
    ) -> dict:
        provider = self.require(provider_id)
        models = provider.resolved_models()
        selected_model = (model or provider.resolved_default_model()).strip()
        if selected_model not in models:
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


def _builtin_provider_specs() -> tuple[ProviderSpec, ...]:
    return (
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
            models=("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
            default_model="gpt-5.6-sol",
            capabilities=ProviderCapabilities(effort=True),
            provider_key="openai",
            efforts=("low", "medium", "high", "xhigh"),
            effort_default="xhigh",
            dispatch="native",
            launch_args=("--codex",),
            logo_aspect=256 / 260,
            models_env="AGENTSTACK_CODEX_MODELS",
        ),
    )


_MANIFEST_FIELDS = frozenset(
    {
        "id",
        "label",
        "program",
        "models",
        "default_model",
        "capabilities",
        "provider_key",
        "efforts",
        "effort_default",
        "dispatch",
        "adapter_script",
        "launch_args",
        "adapter_env",
        "logo_aspect",
        "models_env",
        "required_paths",
        "runtime_commands",
    }
)
_CAPABILITY_FIELDS = frozenset(ProviderCapabilities.__dataclass_fields__)


def _string(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str):
        raise ValueError(f"provider manifest {path}: {field} must be a string")
    return value


def _strings(value: Any, field: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"provider manifest {path}: {field} must be a string list")
    return tuple(value)


def _manifest_capabilities(value: Any, path: Path) -> ProviderCapabilities:
    if not isinstance(value, dict):
        raise ValueError(f"provider manifest {path}: capabilities must be an object")
    unknown = set(value) - _CAPABILITY_FIELDS
    if unknown:
        raise ValueError(
            f"unknown provider capability field in {path}: {sorted(unknown)[0]}"
        )
    if not all(isinstance(item, bool) for item in value.values()):
        raise ValueError(f"provider manifest {path}: capabilities must be booleans")
    return ProviderCapabilities(**value)


def _provider_from_manifest(path: Path) -> ProviderSpec:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid provider manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"provider manifest {path}: expected object")
    unknown = set(payload) - _MANIFEST_FIELDS
    if unknown:
        raise ValueError(
            f"unknown provider manifest field in {path}: {sorted(unknown)[0]}"
        )

    required = {
        "id",
        "label",
        "program",
        "models",
        "default_model",
        "capabilities",
        "provider_key",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(
            f"provider manifest {path}: missing field {sorted(missing)[0]}"
        )

    raw_env = payload.get("adapter_env", {})
    if not isinstance(raw_env, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_env.items()
    ):
        raise ValueError(f"provider manifest {path}: adapter_env must be a string map")
    logo_aspect = payload.get("logo_aspect", 1.0)
    if isinstance(logo_aspect, bool) or not isinstance(logo_aspect, (int, float)):
        raise ValueError(f"provider manifest {path}: logo_aspect must be numeric")

    return ProviderSpec(
        id=_string(payload["id"], "id", path).strip().lower(),
        label=_string(payload["label"], "label", path).strip(),
        program=_string(payload["program"], "program", path).strip(),
        models=_strings(payload["models"], "models", path),
        default_model=_string(payload["default_model"], "default_model", path),
        capabilities=_manifest_capabilities(payload["capabilities"], path),
        provider_key=_string(payload["provider_key"], "provider_key", path).strip(),
        efforts=_strings(payload.get("efforts", []), "efforts", path),
        effort_default=_string(payload.get("effort_default", ""), "effort_default", path),
        dispatch=_string(payload.get("dispatch", "adapter"), "dispatch", path),
        adapter_script=_string(payload.get("adapter_script", ""), "adapter_script", path),
        launch_args=_strings(payload.get("launch_args", []), "launch_args", path),
        adapter_env=tuple(raw_env.items()),
        logo_aspect=float(logo_aspect),
        models_env=_string(payload.get("models_env", ""), "models_env", path),
        required_paths=_strings(payload.get("required_paths", []), "required_paths", path),
        runtime_commands=_strings(payload.get("runtime_commands", []), "runtime_commands", path),
    )


def _manifest_provider_specs(root: Path) -> tuple[ProviderSpec, ...]:
    directory = root / "provider_specs"
    if not directory.is_dir():
        return ()
    return tuple(_provider_from_manifest(path) for path in sorted(directory.glob("*.json")))


def default_provider_registry(
    *,
    available_only: bool = False,
    install_root: str | os.PathLike[str] | None = None,
) -> ProviderRegistry:
    root = Path(install_root or Path(__file__).resolve().parents[2])
    registry = ProviderRegistry(_builtin_provider_specs())
    for spec in _manifest_provider_specs(root):
        if available_only and not spec.is_available(root):
            continue
        registry.register(spec)
    return registry
