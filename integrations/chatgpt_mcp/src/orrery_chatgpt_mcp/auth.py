"""OAuth 2.1 resource-server checks for access tokens presented by ChatGPT.

The gateway is never an authorization server and never accepts ORRERY Mail
owner tokens. A token is accepted only when its signature verifies against
the operator-configured JWKS, its issuer and audience match exactly, it is
unexpired with a bounded lifetime, its subject is an allowlisted operator,
and it carries the scope the operation needs.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

from .config import KNOWN_SCOPES, GatewayConfig

MAX_TOKEN_BYTES = 8192


class AuthError(Exception):
    """A request that must be answered with an OAuth challenge."""

    def __init__(self, error: str, description: str, status: int = 401, scope: str | None = None):
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status
        self.scope = scope


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str]
    client_id: str | None
    expires_at: int


JWKS_TTL = 300.0
JWKS_MIN_REFRESH = 30.0
MAX_JWKS_BYTES = 256 * 1024


class _JwksSource:
    """JWKS from the fixed configured file or URL, never from a token.

    Keys are looked up only in the current key set, so a key removed by the
    issuer stops verifying once the set is refreshed (at most ``JWKS_TTL``).
    An unknown ``kid`` forces at most one refresh per ``JWKS_MIN_REFRESH``.
    Only signing keys (``use`` absent or ``sig``) are usable.
    """

    def __init__(self, config: GatewayConfig, *, clock=time.monotonic, fetch=None):
        import jwt  # oauth mode only; pairing mode runs on the standard library

        self._jwt = jwt
        self._config = config
        self._clock = clock
        self._fetch = fetch or self._fetch_url
        self._lock = threading.Lock()
        self._keys: dict[str, tuple] | None = None
        self._loaded_at = float("-inf")
        self._file_mtime: float | None = None

    def _fetch_url(self) -> bytes:
        import urllib.request

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(urllib.request.Request(self._config.jwks_url, headers={"Accept": "application/json"}), timeout=5) as resp:
            if resp.status != 200:
                raise OSError(f"JWKS HTTP {resp.status}")
            return resp.read(MAX_JWKS_BYTES + 1)

    def _parse(self, raw: bytes) -> dict[str, object]:
        if len(raw) > MAX_JWKS_BYTES:
            raise ValueError("JWKS too large")
        doc = json.loads(raw)
        keys: dict[str, tuple] = {}
        for jwk in doc.get("keys", []) if isinstance(doc, dict) else []:
            if not isinstance(jwk, dict) or not isinstance(jwk.get("kid"), str):
                continue
            if jwk.get("use") not in (None, "sig"):
                continue
            ops = jwk.get("key_ops")
            if ops is not None and "verify" not in ops:
                continue
            try:
                keys[jwk["kid"]] = (self._jwt.PyJWK.from_dict(jwk), jwk.get("alg"))
            except (self._jwt.PyJWKError, self._jwt.InvalidKeyError, ValueError, TypeError):
                continue
        return keys

    def _load(self, *, force: bool) -> None:
        now = self._clock()
        path = self._config.jwks_file
        if path is not None:
            mtime = path.stat().st_mtime
            if self._keys is not None and mtime == self._file_mtime and not force:
                return
            self._keys = self._parse(path.read_bytes())
            self._file_mtime = mtime
            self._loaded_at = now
            return
        stale = now - self._loaded_at >= JWKS_TTL
        if self._keys is not None and not stale and not (force and now - self._loaded_at >= JWKS_MIN_REFRESH):
            return
        self._keys = self._parse(self._fetch())
        self._loaded_at = now

    def key_for(self, kid: str, alg: str):
        with self._lock:
            try:
                self._load(force=False)
                if self._keys is None or kid not in self._keys:
                    self._load(force=True)
            except (OSError, ValueError):
                raise AuthError("invalid_token", "signing keys are unavailable", status=503) from None
            entry = (self._keys or {}).get(kid)
        if entry is None:
            raise AuthError("invalid_token", "unknown signing key")
        jwk, declared_alg = entry
        if declared_alg is not None and declared_alg != alg:
            raise AuthError("invalid_token", "token algorithm does not match its key")
        return jwk.key


def parse_scopes(claims: dict) -> frozenset[str]:
    raw = claims.get("scope", claims.get("scp", ""))
    if isinstance(raw, str):
        items = raw.split()
    elif isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        items = raw
    else:
        return frozenset()
    return frozenset(s for s in items if s in KNOWN_SCOPES)


class TokenVerifier:
    def __init__(self, config: GatewayConfig, *, jwks_source: _JwksSource | None = None):
        self._config = config
        self._keys = jwks_source or _JwksSource(config)
        self._jwt = self._keys._jwt

    def verify_header(self, header_value: str | None) -> Principal:
        if not header_value:
            raise AuthError("invalid_request", "missing bearer token")
        scheme, _, token = header_value.partition(" ")
        if scheme.lower() != "bearer" or not token or " " in token.strip():
            raise AuthError("invalid_request", "authorization must be a single Bearer token")
        return self.verify(token.strip())

    def verify(self, token: str) -> Principal:
        cfg = self._config
        jwt = self._jwt
        if len(token) > MAX_TOKEN_BYTES or token.count(".") != 2:
            raise AuthError("invalid_token", "malformed token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise AuthError("invalid_token", "malformed token") from None
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in cfg.algorithms:
            raise AuthError("invalid_token", "token algorithm is not accepted")
        if not isinstance(kid, str) or not kid:
            raise AuthError("invalid_token", "token has no key id")
        if header.get("typ") not in (None, "JWT", "at+jwt", "application/at+jwt"):
            raise AuthError("invalid_token", "token type is not an access token")
        key = self._keys.key_for(kid, alg)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=cfg.expected_audience,
                issuer=cfg.issuer,
                leeway=cfg.leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"], "verify_signature": True},
            )
        except jwt.ExpiredSignatureError:
            raise AuthError("invalid_token", "token expired") from None
        except jwt.InvalidAudienceError:
            raise AuthError("invalid_token", "token audience does not match this resource") from None
        except jwt.InvalidIssuerError:
            raise AuthError("invalid_token", "token issuer is not trusted") from None
        except jwt.InvalidTokenError:
            raise AuthError("invalid_token", "token verification failed") from None
        now = int(time.time())
        exp, iat = claims.get("exp"), claims.get("iat")
        if not isinstance(exp, int) or not isinstance(iat, int):
            raise AuthError("invalid_token", "token times are malformed")
        if iat > now + cfg.leeway or exp - iat > cfg.max_token_lifetime:
            raise AuthError("invalid_token", "token lifetime is not accepted")
        subject = claims.get("sub")
        if not isinstance(subject, str) or subject not in cfg.allowed_subjects:
            raise AuthError("invalid_token", "subject is not an authorized ORRERY operator", status=403)
        scopes = parse_scopes(claims)
        if not scopes:
            raise AuthError("insufficient_scope", "token carries no ORRERY scope", status=403, scope=" ".join(KNOWN_SCOPES))
        client = claims.get("client_id", claims.get("azp"))
        return Principal(subject=subject, scopes=scopes, client_id=client if isinstance(client, str) else None, expires_at=exp)


def challenge(config: GatewayConfig, error: AuthError) -> str:
    def q(value: str) -> str:
        return value.replace("\\", "").replace('"', "'")

    parts = [f'resource_metadata="{config.resource_metadata_url}"', f'error="{q(error.error)}"', f'error_description="{q(error.description)}"']
    if error.scope:
        parts.append(f'scope="{q(error.scope)}"')
    return "Bearer " + ", ".join(parts)
