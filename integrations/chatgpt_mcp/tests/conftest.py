from __future__ import annotations

import threading

import pytest

from chatgpt_gateway_support import ISSUER, OPERATOR, RESOURCE, FakeDashboard, Keys, free_port


@pytest.fixture
def keys(tmp_path):
    return Keys(tmp_path)


@pytest.fixture
def dashboard():
    d = FakeDashboard()
    yield d
    d.close()


@pytest.fixture
def make_gateway(tmp_path, keys, dashboard):
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.server import build_server

    started = []

    def make(**overrides):
        cfg = dict(
            auth_mode="oauth",
            resource=RESOURCE,
            issuer=ISSUER,
            allowed_subjects=frozenset({OPERATOR, "user|operator-2"}),
            jwks_file=keys.jwks_file,
            port=free_port(),
            dashboard_url=dashboard.url,
            state_dir=tmp_path / "state",
        )
        cfg.update(overrides)
        config = GatewayConfig(**cfg)
        server = build_server(config)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        started.append(server)
        return config, server

    yield make
    for s in started:
        s.shutdown()
        s.server_close()
