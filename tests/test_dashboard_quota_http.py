from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import dashboard.quota_server as quota_server


class _FakeQuotaService:
    def read_all(self) -> dict[str, object]:
        return {
            "ts": 1000,
            "degraded": True,
            "providers": [
                {
                    "provider": "codex",
                    "status": "ok",
                    "source": "fixture",
                    "observed_at": 999,
                    "buckets": [
                        {
                            "id": "5h",
                            "label": "5h",
                            "scope": "account",
                            "used_percent": 25.0,
                            "remaining_percent": 75.0,
                            "window_seconds": 18000,
                            "resets_at": 1200,
                            "quality": "exact",
                        }
                    ],
                },
                {
                    "provider": "antigravity",
                    "status": "unavailable",
                    "source": "fixture",
                    "observed_at": 1000,
                    "buckets": [],
                    "reason": "offline",
                },
            ],
        }


def test_api_quotas_returns_partial_failure_as_200(monkeypatch):
    monkeypatch.setattr(quota_server, "QUOTA_SERVICE", _FakeQuotaService())
    server = ThreadingHTTPServer(("127.0.0.1", 0), quota_server.Handler)
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/quotas",
            timeout=5,
        ) as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            payload = json.loads(response.read())
    finally:
        worker.join(timeout=6)
        server.server_close()

    assert not worker.is_alive()
    assert payload["degraded"] is True
    assert [provider["status"] for provider in payload["providers"]] == [
        "ok",
        "unavailable",
    ]
    assert payload["providers"][0]["buckets"][0]["remaining_percent"] == 75.0
