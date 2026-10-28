"""Loopback client for the existing ORRERY dashboard API.

Only the routes in ``_ROUTES`` can be called. The base URL is a validated
loopback literal from configuration and redirects are never followed, so no
tool argument can steer a request elsewhere (SSRF) or onto another route.
"""
from __future__ import annotations

import http.client
import json
from urllib.parse import quote, urlsplit

MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_ROUTES = {
    ("GET", "/api/version"),
    ("GET", "/api/agents"),
    ("GET", "/api/spawn-status"),
    ("GET", "/api/name-status"),
    ("GET", "/api/suggest-name"),
    ("POST", "/api/spawn"),
}


class DashboardError(Exception):
    """The dashboard could not give a usable answer.

    ``outcome_unknown`` is set when a write may have been applied even
    though no answer arrived; callers must reconcile instead of retrying.
    """

    def __init__(self, message: str, *, status: int | None = None, body: object = None, outcome_unknown: bool = False):
        super().__init__(message)
        self.status = status
        self.body = body
        self.outcome_unknown = outcome_unknown


class DashboardClient:
    def __init__(self, base_url: str, *, read_timeout: float = 10.0, write_timeout: float = 100.0):
        parts = urlsplit(base_url)
        self._host = parts.hostname
        self._port = parts.port or 80
        self._host_header = parts.netloc
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout

    def _request(self, method: str, path: str, query: dict[str, str] | None = None, body: dict | None = None) -> tuple[int, object]:
        if (method, path) not in _ROUTES:
            raise DashboardError(f"route {method} {path} is not allowed")
        target = path
        if query:
            target += "?" + "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in query.items())
        data = None
        headers = {"Host": self._host_header, "Accept": "application/json", "User-Agent": "orrery-chatgpt-mcp"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        is_write = method != "GET"
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self._write_timeout if is_write else self._read_timeout)
        sent = False
        try:
            conn.request(method, target, body=data, headers=headers)
            sent = True
            resp = conn.getresponse()
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            status = resp.status
        except (OSError, http.client.HTTPException) as exc:
            # Once the request bytes are out, a spawn may have happened.
            raise DashboardError(f"dashboard unreachable: {type(exc).__name__}", outcome_unknown=is_write and sent) from None
        finally:
            conn.close()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DashboardError("dashboard response too large", status=status, outcome_unknown=is_write)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            raise DashboardError("dashboard returned non-JSON", status=status, outcome_unknown=is_write) from None
        if 300 <= status < 400:
            raise DashboardError("dashboard redirected; refusing to follow", status=status, outcome_unknown=is_write)
        return status, parsed

    def version(self) -> dict:
        status, body = self._request("GET", "/api/version")
        if status != 200 or not isinstance(body, dict):
            raise DashboardError("unexpected /api/version answer", status=status)
        return body

    def agents(self) -> list[dict]:
        status, body = self._request("GET", "/api/agents")
        if status == 503:
            raise DashboardError("dashboard busy; retry shortly", status=status)
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("agents"), list):
            raise DashboardError("unexpected /api/agents answer", status=status)
        return [row for row in body["agents"] if isinstance(row, dict)]

    def spawn_status(self, name: str) -> tuple[int, dict | None]:
        status, body = self._request("GET", "/api/spawn-status", {"name": name})
        if status == 404:
            return 404, None
        if status != 200 or not isinstance(body, dict):
            raise DashboardError("unexpected /api/spawn-status answer", status=status)
        return status, body

    def name_status(self, name: str) -> str:
        status, body = self._request("GET", "/api/name-status", {"name": name})
        if status != 200 or not isinstance(body, dict):
            return "unknown"
        value = body.get("status")
        return value if value in ("available", "occupied", "unknown") else "unknown"

    def suggest_name(self) -> str | None:
        try:
            status, body = self._request("GET", "/api/suggest-name")
        except DashboardError:
            return None
        if status == 200 and isinstance(body, dict) and isinstance(body.get("name"), str):
            return body["name"]
        return None

    def spawn(self, payload: dict) -> tuple[int, dict]:
        status, body = self._request("POST", "/api/spawn", body=payload)
        if not isinstance(body, dict):
            raise DashboardError("unexpected /api/spawn answer", status=status, outcome_unknown=not payload.get("dry_run"))
        return status, body
