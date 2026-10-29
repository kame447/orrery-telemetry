"""End to end over real ORRERY Mail: an isolated Mail process (temp HOME/DB/socket/port),
synthetic identities only. Never touches the live Mail, dashboard, launchd or Tunnel."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from chatgpt_gateway_support import FakeDashboard, free_port
from test_chatgpt_gateway_local import send
from test_chatgpt_gateway_security import rpc

ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT / "integrations/codex_app/src", ROOT / "packages/agentstack_mail/src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agentstack_codex_app.agent_mail_client import AgentMailClient, AgentMailError, HttpJsonRpcTransport  # noqa: E402
from agentstack_codex_app.daemon_runtime import KIND, create_identity  # noqa: E402

LIVE_PORTS = {8765, 8770, 8790, 8791, 9250, 18765}
WORKER_TOKEN = "synthetic-worker-owner-token-000000000000"
OTHER_TOKEN = "synthetic-other-owner-token-0000000000000"


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


@pytest.fixture
def mail(tmp_path, request):
    tmp_path.chmod(0o700)
    scratch = tempfile.TemporaryDirectory(prefix="mcpmsg-", dir="/tmp")  # short Unix socket path
    control = Path(scratch.name) / "m.sock"
    port = free_port()
    assert port not in LIVE_PORTS
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT / "packages/agentstack_mail/src"),
        "AGENTSTACK_MAIL_ENV_FILE": str(tmp_path / "missing.env"),
        "AGENTSTACK_MAIL_DATABASE_URL": "sqlite+aiosqlite:///" + str(tmp_path / "mail.sqlite"),
        "AGENTSTACK_MAIL_STORAGE_ROOT": str(tmp_path / "archive"),
        "AGENTSTACK_MAIL_NOTIFICATIONS_SIGNALS_DIR": str(tmp_path / "signals"),
        "AGENTSTACK_MAIL_MANAGEMENT_SOCKET": str(control),
        "AGENTSTACK_MAIL_HTTP_HOST": "127.0.0.1",
        "AGENTSTACK_MAIL_HTTP_PORT": str(port),
        "AGENTSTACK_MAIL_HTTP_PATH": "/api/",
        "AGENTSTACK_MAIL_AGENT_NAME_ENFORCEMENT_MODE": "passthrough",
        "AGENTSTACK_MAIL_NOTIFICATIONS_ENABLED": "false",
        "AGENTSTACK_MAIL_LOG_LEVEL": "WARNING",
        "AGENTSTACK_MAIL_HTTP_REQUEST_LOG_ENABLED": "false",
        **getattr(request, "param", {}),
    }
    log = open(tmp_path / "mail.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "agentstack_mail.cli"], env=env, cwd=tmp_path,
                            stdout=log, stderr=log, start_new_session=True)
    url = f"http://127.0.0.1:{port}/api/"
    probe = AgentMailClient(HttpJsonRpcTransport(url, timeout=1, allow_redirects=False))
    client = AgentMailClient(HttpJsonRpcTransport(url, allow_redirects=False))
    try:
        deadline = time.monotonic() + 30
        while True:
            assert proc.poll() is None, "isolated Mail process exited"
            try:
                health = probe._call_tool_object("health_check", {})
                if control.exists():
                    break
            except AgentMailError:
                pass
            assert time.monotonic() < deadline, "isolated Mail startup deadline"
            time.sleep(0.1)
        conn = _write(tmp_path / "connection.json", {
            "kind": "orrery-mail-connection-v1",
            "expected_server_instance_id": health["server_instance_id"],
            "mcp_url": url,
            "management_socket": str(control),
            "runtime_dir": str(tmp_path),
            "http_bearer_mode": "disabled",
        })
        project = str(tmp_path / "project")
        client._call_tool_object("ensure_project", {"human_key": project})
        worker = client._call_tool_object("register_agent", {"project_key": project, "name": "ExampleWorker", "program": "fixture-worker",
                                                              "model": "deterministic", "registration_token": WORKER_TOKEN})
        client._call_tool_object("register_agent", {"project_key": project, "name": "ExampleOther", "program": "fixture-worker",
                                                     "model": "deterministic", "registration_token": OTHER_TOKEN})
        # The operator's one-time step (agentstack-daemon create --operator), on synthetic Mail.
        receipt = create_identity(conn, project_key=project, name="ChatRelay", journal_path=tmp_path / "creation.json")
        profile = _write(tmp_path / "daemon.json", {
            "kind": KIND, "name": "ChatRelay", "agent_id": receipt["agent_id"], "project_key": project,
            "connection": str(conn), "peers": [{"name": "ExampleWorker", "id": worker["id"]}],
            "pause_path": str(tmp_path / "paused"), "lock_path": str(tmp_path / "daemon.lock"),
        })
        yield {"client": client, "project": project, "profile": profile, "receipt": receipt, "worker": worker,
               "proc": proc, "root": tmp_path, "log": tmp_path / "mail.log", "instance": health["server_instance_id"]}
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=15)
        log.close()
        scratch.cleanup()


@pytest.fixture
def gateway(mail, tmp_path):
    from orrery_chatgpt_mcp import pairing
    from orrery_chatgpt_mcp.config import GatewayConfig
    from orrery_chatgpt_mcp.server import build_server

    dash = FakeDashboard()  # answers 404 for /api/edge-messages: observation is "unavailable"
    state = tmp_path / "gw-state"
    secret = pairing.create_secret(state)
    servers = []

    def make(**overrides):
        cfg = dict(pairing_secret=secret, port=free_port(), dashboard_url=dash.url, state_dir=state, message_enabled=True,
                   message_profile=mail["profile"], message_helper_root=ROOT)
        cfg.update(overrides)
        config = GatewayConfig(**cfg).validate()
        server = build_server(config)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return config

    yield make
    for s in servers:
        s.shutdown()
        s.server_close()
    dash.close()


def call(config, name, args):
    status, body = send(config, rpc("tools/call", {"name": name, "arguments": args}))
    assert status == 200, body
    return body["result"]


def inbox(mail, name="ExampleWorker", token=WORKER_TOKEN):
    return mail["client"].fetch_inbox(project_key=mail["project"], agent_name=name, registration_token=token,
                                      include_bodies=True, limit=50)


def test_message_reaches_the_fictional_agent_once_from_the_dedicated_sender(mail, gateway):
    config = gateway()
    caps = call(config, "orrery_status", {})["structuredContent"]["capabilities"]
    assert caps["message_existing_agent"] is True and caps["message_recipients"] == ["ExampleWorker"]
    preview = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "README の誤字を直してください。",
                                                       "subject": "追加指示"})["structuredContent"]
    msg = preview["message"]
    assert msg["to"]["mail_id"] == mail["worker"]["id"] and msg["to"]["program"] == "fixture-worker"
    assert msg["from"] == {"name": "ChatRelay", "mail_id": mail["receipt"]["agent_id"]}
    assert msg["mail_instance"] == mail["instance"]
    assert inbox(mail) == []  # preview sends nothing

    args = {"preview_id": preview["preview_id"], "idempotency_key": "e2e-key-0001", "content_sha256": preview["content_sha256"]}
    out = call(config, "orrery_message_commit", args)
    assert out["isError"] is False, out
    result = out["structuredContent"]
    assert result["outcome"] == "accepted" and isinstance(result["message"]["mail_message_id"], int)

    received = inbox(mail)
    assert len(received) == 1
    got = received[0]
    assert got["from"] == "ChatRelay" and got["sender_id"] == mail["receipt"]["agent_id"]
    assert got["subject"] == "[ChatGPT] 追加指示" and got["body_md"] == msg["body"]
    assert got["id"] == result["message"]["mail_message_id"]

    # Retry with the same key, a second gateway on the same state (restart), and status: still one message.
    assert call(config, "orrery_message_commit", args)["structuredContent"]["replayed"] is True
    restarted = gateway()
    assert call(restarted, "orrery_message_commit", args)["structuredContent"]["outcome"] == "accepted"
    status = call(restarted, "orrery_message_status", {"idempotency_key": "e2e-key-0001"})["structuredContent"]
    assert status["message"]["state"] == "accepted" and status["observation"]["mail_record"] == "unavailable"
    assert len(inbox(mail)) == 1
    assert inbox(mail, "ExampleOther", OTHER_TOKEN) == []


def test_unlisted_agent_cannot_be_messaged(mail, gateway):
    config = gateway()
    out = call(config, "orrery_message_preview", {"recipient": "ExampleOther", "instruction": "hello"})
    assert out["isError"] is True and out["structuredContent"]["error"]["code"] == "recipient_not_allowed"
    assert inbox(mail, "ExampleOther", OTHER_TOKEN) == []


def test_recipient_retired_after_preview_is_not_sent(mail, gateway):
    config = gateway()
    preview = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "do it"})["structuredContent"]
    mail["client"].retire_agent(project_key=mail["project"], agent_name="ExampleWorker", registration_token=WORKER_TOKEN)
    out = call(config, "orrery_message_commit", {"preview_id": preview["preview_id"], "idempotency_key": "e2e-key-0002",
                                                 "content_sha256": preview["content_sha256"]})
    assert out["isError"] is True and out["structuredContent"]["error"]["code"] == "recipient_changed"
    status = call(config, "orrery_message_status", {"idempotency_key": "e2e-key-0002"})["structuredContent"]
    assert status["message"]["state"] == "rejected"


def test_paused_sender_and_stopped_mail_fail_closed(mail, gateway):
    config = gateway()
    preview = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "do it"})["structuredContent"]
    base = {"preview_id": preview["preview_id"], "content_sha256": preview["content_sha256"]}
    pause = Path(mail["root"] / "paused")
    pause.write_text("{}")
    out = call(config, "orrery_message_commit", {**base, "idempotency_key": "e2e-key-0003"})
    assert out["isError"] is True and out["structuredContent"]["error"]["code"] == "not_sent"
    pause.unlink()
    assert inbox(mail) == []

    second = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "again"})["structuredContent"]
    os.killpg(mail["proc"].pid, signal.SIGTERM)
    mail["proc"].wait(timeout=15)
    out = call(config, "orrery_message_commit", {"preview_id": second["preview_id"], "idempotency_key": "e2e-key-0004",
                                                 "content_sha256": second["content_sha256"]})
    assert out["isError"] is True and out["structuredContent"]["error"]["code"] == "not_sent"


def test_gateway_without_message_settings_offers_no_message_tools(mail, gateway):
    config = gateway(message_enabled=False, message_profile=None)
    status, body = send(config, rpc("tools/list"))
    names = {t["name"] for t in body["result"]["tools"]}
    assert not any(n.startswith("orrery_message") for n in names)
    out = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "x"})
    assert out["isError"] is True
    assert inbox(mail) == []


def test_mail_reservation_conflict_is_reported_as_not_sent(mail, gateway):
    """Audit F2: Mail's structured pre-delivery refusal is not an accepted message."""
    config = gateway()
    mail["client"].reserve_files(project_key=mail["project"], agent_name="ExampleOther", paths=["messages/**"],
                                 ttl_seconds=600, registration_token=OTHER_TOKEN)
    preview = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "blocked"})["structuredContent"]
    out = call(config, "orrery_message_commit", {"preview_id": preview["preview_id"], "idempotency_key": "e2e-key-0005",
                                                 "content_sha256": preview["content_sha256"]})
    assert out["isError"] is True
    assert out["structuredContent"]["error"] == {**out["structuredContent"]["error"], "code": "not_sent",
                                                 "reason": "mail-file-reservation-conflict"}
    assert inbox(mail) == []
    status = call(config, "orrery_message_status", {"idempotency_key": "e2e-key-0005"})["structuredContent"]
    assert status["message"]["state"] == "rejected"


def test_received_message_matches_the_preview_exactly(mail, gateway):
    """Audit F3: what arrives is byte-for-byte what was confirmed, with Mail's default image handling on."""
    config = gateway()
    text = "1. `pytest -q` を実行\n2. 結果を [PR](https://example.test/pr) に書く\n\tインデント付き"
    preview = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": text})["structuredContent"]
    out = call(config, "orrery_message_commit", {"preview_id": preview["preview_id"], "idempotency_key": "e2e-key-0006",
                                                 "content_sha256": preview["content_sha256"]})["structuredContent"]
    assert out["message"]["body_verified"] is None  # Mail's receipt carries no body; the inbox check below is the proof
    (got,) = inbox(mail)
    assert got["body_md"] == preview["message"]["body"] and got["subject"] == preview["message"]["subject"]
    assert got["importance"] == "normal"
    refused = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": "![x](example.png)"})
    assert refused["isError"] is True


@pytest.mark.parametrize("mail", [{"AGENTSTACK_MAIL_TOOLS_LOG_ENABLED": "false"}], indirect=True)
def test_no_body_in_mail_log_when_tool_logging_is_off(mail, gateway):
    """Audit R3: with Mail's tool log off, the instruction body reaches the inbox and no log."""
    config = gateway()
    marker = "BODY-MARKER-" + "q7w3e9r1"
    preview = call(config, "orrery_message_preview", {"recipient": "ExampleWorker", "instruction": f"do {marker}", "subject": "plain"})["structuredContent"]
    out = call(config, "orrery_message_commit", {"preview_id": preview["preview_id"], "idempotency_key": "e2e-key-0007",
                                                 "content_sha256": preview["content_sha256"]})
    assert out["isError"] is False, out
    log_text = mail["log"].read_text(errors="replace")
    assert marker not in log_text
    assert marker in inbox(mail)[0]["body_md"]
