"""Wire constants of the local enrollment control plane, importable without the server.

Clients of the management socket (the operator CLI and the deterministic
daemon helper) need only these values. Keeping them free of SQLAlchemy lets a
plain-stdlib Python, such as the ChatGPT MCP gateway, use the daemon helper.
"""

from __future__ import annotations

import hashlib

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 16 * 1024


def credential_fingerprint(value: str | None) -> str | None:
    """Return a non-secret, stable diagnostic fingerprint."""

    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
