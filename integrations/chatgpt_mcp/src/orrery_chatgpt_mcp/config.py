"""Gateway configuration. Every security-relevant value is explicit.

There is no unauthenticated mode and no non-loopback bind: a configuration
that would need either is refused at startup instead of being repaired.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

AUTH_PAIRING = "pairing"
AUTH_OAUTH = "oauth"
APPROVAL_LOCAL = "local"
APPROVAL_CHAT = "chat"
SCOPE_READ = "orrery.read"
SCOPE_SPAWN = "orrery.spawn"
SCOPE_MESSAGE = "orrery.message"
KNOWN_SCOPES = (SCOPE_READ, SCOPE_SPAWN, SCOPE_MESSAGE)
ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "EdDSA"})
SPAWN_PROVIDERS = ("claude", "codex")
_ALIAS_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
PAIRING_RE = re.compile(r"[A-Za-z0-9_-]{43}")
LOCAL_SUBJECT = "local-operator"


class ConfigError(ValueError):
    """Raised for a configuration the gateway refuses to start with."""


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _https_url(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment or parts.query:
        raise ConfigError(f"{name} must be an https URL without credentials, query or fragment")
    return value.rstrip("/") if parts.path not in ("", "/") else f"https://{parts.netloc}"


@dataclass(frozen=True)
class GatewayConfig:
    auth_mode: str = AUTH_PAIRING
    pairing_secret: str | None = None
    spawn_approval: str = APPROVAL_LOCAL
    resource: str | None = None
    issuer: str | None = None
    allowed_subjects: frozenset[str] = frozenset()
    jwks_url: str | None = None
    jwks_file: Path | None = None
    audience: str | None = None
    algorithms: frozenset[str] = frozenset({"RS256", "ES256"})
    bind_host: str = "127.0.0.1"
    port: int = 8790
    dashboard_url: str = "http://127.0.0.1:8770"
    allowed_origins: frozenset[str] = frozenset()
    spawn_enabled: bool = False
    spawn_dirs: Mapping[str, str] = field(default_factory=dict)
    state_dir: Path | None = None
    max_token_lifetime: int = 24 * 3600
    leeway: int = 60
    rate_per_minute: int = 60
    unauth_rate_per_minute: int = 30
    # Messages to existing agents: off unless the operator names a dedicated
    # orrery-daemon sender profile (its peer list is the recipient allowlist).
    message_enabled: bool = False
    message_profile: Path | None = None
    message_approval: str = APPROVAL_CHAT
    message_helper_root: Path | None = None
    message_max_per_hour: int = 20

    @property
    def expected_audience(self) -> str:
        return self.audience or self.resource

    @property
    def resource_host(self) -> str | None:
        return urlsplit(self.resource).netloc.lower() if self.resource else None

    @property
    def mcp_path(self) -> str:
        """The MCP endpoint path. In pairing mode it embeds the secret: never log it."""
        if self.auth_mode == AUTH_PAIRING:
            return f"/mcp/{self.pairing_secret}"
        path = urlsplit(self.resource).path
        return path if path not in ("", "/") else "/mcp"

    @property
    def display_mcp_path(self) -> str:
        return "/mcp/<pairing-secret>" if self.auth_mode == AUTH_PAIRING else self.mcp_path

    @property
    def resource_metadata_url(self) -> str:
        parts = urlsplit(self.resource)
        suffix = "" if parts.path in ("", "/") else parts.path
        return f"https://{parts.netloc}/.well-known/oauth-protected-resource{suffix}"

    def validate(self) -> "GatewayConfig":
        if not _is_loopback_host(self.bind_host):
            raise ConfigError("bind host must be loopback; expose the gateway through Secure MCP Tunnel or an authenticated HTTPS proxy, never 0.0.0.0")
        if not (0 < self.port < 65536):
            raise ConfigError("port out of range")
        dash = urlsplit(self.dashboard_url)
        if dash.scheme != "http" or not dash.hostname or not _is_loopback_host(dash.hostname) or dash.path not in ("", "/"):
            raise ConfigError("dashboard URL must be http://<loopback>:<port> with no path")
        if self.auth_mode == AUTH_PAIRING:
            if not self.pairing_secret or not PAIRING_RE.fullmatch(self.pairing_secret):
                raise ConfigError("pairing mode needs a pairing secret; run `orrery-chatgpt-mcp pair` once on this Mac")
            if self.resource or self.issuer or self.allowed_subjects or self.jwks_url or self.jwks_file:
                raise ConfigError("OAuth settings are set but ORRERY_MCP_AUTH is not oauth; refusing an ambiguous setup")
            if self.allowed_origins:
                raise ConfigError("pairing mode accepts no browser origins")
        elif self.auth_mode == AUTH_OAUTH:
            if not self.resource or not self.issuer:
                raise ConfigError("oauth mode needs ORRERY_MCP_RESOURCE and ORRERY_MCP_ISSUER")
            if not self.allowed_subjects or any(not s or s != s.strip() for s in self.allowed_subjects):
                raise ConfigError("ORRERY_MCP_ALLOWED_SUBJECTS must name at least one operator subject")
            if bool(self.jwks_url) == bool(self.jwks_file):
                raise ConfigError("set exactly one of ORRERY_MCP_JWKS_URL or ORRERY_MCP_JWKS_FILE")
            if not self.algorithms or not self.algorithms <= ALLOWED_ALGORITHMS:
                raise ConfigError(f"algorithms must be a subset of {sorted(ALLOWED_ALGORITHMS)}")
        else:
            raise ConfigError("ORRERY_MCP_AUTH must be pairing or oauth")
        if self.spawn_approval not in (APPROVAL_LOCAL, APPROVAL_CHAT):
            raise ConfigError("ORRERY_MCP_SPAWN_APPROVAL must be local or chat")
        if self.max_token_lifetime <= 0 or not (0 <= self.leeway <= 300):
            raise ConfigError("invalid token lifetime or leeway")
        if self.rate_per_minute <= 0 or self.unauth_rate_per_minute <= 0:
            raise ConfigError("rate limits must be positive")
        for alias, path in self.spawn_dirs.items():
            if not _ALIAS_RE.fullmatch(alias):
                raise ConfigError(f"spawn dir alias {alias!r} must match {_ALIAS_RE.pattern}")
            if not os.path.isabs(path):
                raise ConfigError(f"spawn dir for {alias!r} must be absolute")
        if self.spawn_enabled and not self.spawn_dirs:
            raise ConfigError("spawn is enabled but ORRERY_MCP_SPAWN_DIRS lists no directory")
        if self.spawn_enabled and self.state_dir is None:
            raise ConfigError("spawn needs a state directory for idempotency records")
        if self.message_approval not in (APPROVAL_LOCAL, APPROVAL_CHAT):
            raise ConfigError("ORRERY_MCP_MESSAGE_APPROVAL must be chat or local")
        if not (1 <= self.message_max_per_hour <= 200):
            raise ConfigError("ORRERY_MCP_MESSAGE_MAX_PER_HOUR must be 1..200")
        if self.message_enabled:
            if self.message_profile is None or not self.message_profile.is_absolute():
                raise ConfigError("messages are enabled but ORRERY_MCP_MESSAGE_PROFILE is not an absolute path to the sender profile")
            if self.message_helper_root is None or not self.message_helper_root.is_absolute():
                raise ConfigError("messages need ORRERY_MCP_MESSAGE_HELPER_ROOT, the absolute ORRERY checkout that holds the daemon helper")
            if self.state_dir is None:
                raise ConfigError("messages need a state directory for idempotency records")
        elif self.message_profile is not None:
            raise ConfigError("ORRERY_MCP_MESSAGE_PROFILE is set but ORRERY_MCP_ENABLE_MESSAGE is not 1; refusing an ambiguous setup")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewayConfig":
        env = os.environ if env is None else env

        def need(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise ConfigError(f"{name} is required")
            return value

        mode = env.get("ORRERY_MCP_AUTH", AUTH_PAIRING).strip() or AUTH_PAIRING
        home = Path(env.get("AGENTSTACK_HOME") or Path.home() / ".agentstack")
        state = env.get("ORRERY_MCP_STATE_DIR", "").strip()
        state_dir = Path(state) if state else default_state_dir(home)
        spawn_dirs: dict[str, str] = {}
        for item in env.get("ORRERY_MCP_SPAWN_DIRS", "").split(","):
            item = item.strip()
            if not item:
                continue
            alias, sep, path = item.partition("=")
            if not sep:
                raise ConfigError("ORRERY_MCP_SPAWN_DIRS entries are alias=/absolute/path")
            spawn_dirs[alias.strip()] = path.strip()
        enabled = env.get("ORRERY_MCP_ENABLE_SPAWN", "").strip()
        if enabled not in ("", "0", "1"):
            raise ConfigError("ORRERY_MCP_ENABLE_SPAWN must be 0 or 1")
        message_enabled = env.get("ORRERY_MCP_ENABLE_MESSAGE", "").strip()
        if message_enabled not in ("", "0", "1"):
            raise ConfigError("ORRERY_MCP_ENABLE_MESSAGE must be 0 or 1")
        message_profile = env.get("ORRERY_MCP_MESSAGE_PROFILE", "").strip()
        helper_root = env.get("ORRERY_MCP_MESSAGE_HELPER_ROOT", "").strip()
        try:
            port = int(env.get("ORRERY_MCP_PORT", "8790"))
            max_life = int(env.get("ORRERY_MCP_MAX_TOKEN_LIFETIME", str(24 * 3600)))
            rate = int(env.get("ORRERY_MCP_RATE_PER_MINUTE", "60"))
            message_rate = int(env.get("ORRERY_MCP_MESSAGE_MAX_PER_HOUR", "20"))
        except ValueError as exc:
            raise ConfigError(f"numeric setting is not an integer: {exc}") from None
        common = dict(
            auth_mode=mode,
            bind_host=env.get("ORRERY_MCP_BIND_HOST", "127.0.0.1").strip(),
            port=port,
            dashboard_url=env.get("ORRERY_MCP_DASHBOARD_URL", "http://127.0.0.1:8770").strip().rstrip("/"),
            spawn_enabled=enabled == "1",
            spawn_dirs=spawn_dirs,
            spawn_approval=env.get("ORRERY_MCP_SPAWN_APPROVAL", APPROVAL_LOCAL).strip(),
            state_dir=state_dir,
            rate_per_minute=rate,
            message_enabled=message_enabled == "1",
            message_profile=Path(message_profile) if message_profile else None,
            message_approval=env.get("ORRERY_MCP_MESSAGE_APPROVAL", APPROVAL_CHAT).strip(),
            message_helper_root=Path(helper_root) if helper_root else default_helper_root(),
            message_max_per_hour=message_rate,
        )
        if mode != AUTH_OAUTH:
            for name in ("ORRERY_MCP_RESOURCE", "ORRERY_MCP_ISSUER", "ORRERY_MCP_JWKS_URL", "ORRERY_MCP_JWKS_FILE", "ORRERY_MCP_ALLOWED_SUBJECTS", "ORRERY_MCP_ALLOWED_ORIGINS"):
                if env.get(name, "").strip():
                    raise ConfigError(f"{name} is an oauth-mode setting but ORRERY_MCP_AUTH is {mode!r}")
            from .pairing import read_secret

            return cls(pairing_secret=read_secret(state_dir), **common).validate()
        resource = _https_url("ORRERY_MCP_RESOURCE", need("ORRERY_MCP_RESOURCE"))
        issuer = need("ORRERY_MCP_ISSUER")
        if urlsplit(issuer).scheme != "https":
            raise ConfigError("ORRERY_MCP_ISSUER must be an https issuer identifier")
        jwks_url = env.get("ORRERY_MCP_JWKS_URL", "").strip() or None
        if jwks_url:
            _https_url("ORRERY_MCP_JWKS_URL", jwks_url)
        jwks_file = env.get("ORRERY_MCP_JWKS_FILE", "").strip() or None
        subjects = frozenset(s.strip() for s in need("ORRERY_MCP_ALLOWED_SUBJECTS").split(",") if s.strip())
        origins = frozenset(o.strip().rstrip("/") for o in env.get("ORRERY_MCP_ALLOWED_ORIGINS", "").split(",") if o.strip())
        algorithms = frozenset(a.strip() for a in env.get("ORRERY_MCP_ALGORITHMS", "RS256,ES256").split(",") if a.strip())
        return cls(
            resource=resource,
            issuer=issuer,
            allowed_subjects=subjects,
            jwks_url=jwks_url,
            jwks_file=Path(jwks_file) if jwks_file else None,
            audience=env.get("ORRERY_MCP_AUDIENCE", "").strip() or None,
            algorithms=algorithms,
            allowed_origins=origins,
            max_token_lifetime=max_life,
            **common,
        ).validate()


def default_helper_root() -> Path | None:
    """The ORRERY checkout this package runs from, when it is one (a pinned
    gateway-only release is not: it must name the checkout explicitly)."""
    root = Path(__file__).resolve().parents[4]
    return root if (root / "integrations/codex_app/src/agentstack_codex_app/daemon_runtime.py").is_file() else None


def default_state_dir(home: Path) -> Path:
    return home / "runtime" / "chatgpt-mcp"
