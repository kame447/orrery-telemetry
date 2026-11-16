"""Fail-closed proof of a running fresh MCP process and its effective authority.

This module does not install, restart or stop anything. The caller supplies an
operator-verified expectation and a private pairing secret. It never returns
that secret or any arbitrary response body.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import os
import re
import socket
import subprocess
from typing import Callable


class ProbeError(Exception):
    """An intentionally non-sensitive verification failure."""


@dataclass(frozen=True)
class Authority:
    mail_instance_id: str
    sender_agent_id: int
    recipients: tuple[tuple[int, str, str], ...]

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{32}", self.mail_instance_id):
            raise ValueError("invalid expected instance")
        if type(self.sender_agent_id) is not int or self.sender_agent_id <= 0:
            raise ValueError("invalid expected sender")
        if (not self.recipients or len(set(self.recipients)) != len(self.recipients)
                or len({row[0] for row in self.recipients}) != len(self.recipients)):
            raise ValueError("invalid expected recipient set")
        for recipient in self.recipients:
            if (not isinstance(recipient, tuple) or len(recipient) != 3
                    or type(recipient[0]) is not int or recipient[0] <= 0
                    or not isinstance(recipient[1], str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", recipient[1])
                    or recipient[2] != "to"):
                raise ValueError("invalid expected recipient")


READ_TOOLS = frozenset({"orrery_status", "orrery_list_agents", "orrery_get_agent"})
MESSAGE_TOOLS = frozenset({"orrery_message_preview", "orrery_message_commit", "orrery_message_status"})


def job_pid(label: str, plist_path: str, *, runner: Callable = subprocess.run) -> int:
    """Bind the expected LaunchAgent's loaded plist to its current PID."""
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{3,128}", label)
            or not os.path.isabs(plist_path)):
        raise ProbeError("INVALID_SERVICE_IDENTITY")
    try:
        done = runner(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                      capture_output=True, text=True, timeout=10)
        if done.returncode != 0:
            raise ValueError
        path = re.findall(r"^\tpath = ([^\r\n]+)$", done.stdout, re.M)
        pids = re.findall(r"^\tpid = ([0-9]+)$", done.stdout, re.M)
        if len(path) != 1 or len(pids) != 1 or path[0] != plist_path:
            raise ValueError
        pid = int(pids[0])
        if pid <= 1:
            raise ValueError
        return pid
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        raise ProbeError("SERVICE_PROCESS_UNVERIFIED") from None


def _owns_connection(sock: socket.socket, port: int, pid: int, runner: Callable) -> bool:
    """Check the server end of this exact TCP connection, not just the port."""
    local = sock.getsockname()[1]
    want = f"n127.0.0.1:{port}->127.0.0.1:{local}"
    try:
        done = runner(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
                       f"-iTCP@127.0.0.1:{local}", "-Fn"],
                      capture_output=True, text=True, timeout=10)
        return done.returncode == 0 and want in done.stdout.splitlines()
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return False


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject ambiguous JSON keys at every depth of an attestation response."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous MCP response")
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> None:
    """JSON-RPC responses must not contain Python's nonstandard NaN/Infinity."""
    raise ValueError("invalid JSON constant")


def _request(conn: http.client.HTTPConnection, pinned_sock: socket.socket,
             secret: str, method: str, number: int, params=None) -> dict:
    body = {"jsonrpc": "2.0", "id": number, "method": method}
    if params is not None:
        body["params"] = params
    if conn.sock is not pinned_sock or pinned_sock.fileno() < 0:
        raise ProbeError("VERIFIED_CONNECTION_CLOSED")
    conn.request("POST", "/mcp/" + secret, body=json.dumps(body, separators=(",", ":")),
                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    response = conn.getresponse()
    data = response.read(1024 * 1024 + 1)
    media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
    if response.status != 200 or len(data) > 1024 * 1024 or media_type != "application/json":
        raise ProbeError("MCP_PROTOCOL_UNVERIFIED")
    payload = json.loads(data, object_pairs_hook=_unique_object,
                         parse_constant=_invalid_json_constant)
    if (not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0"
            or "error" in payload
            or type(payload.get("id")) is not int or payload["id"] != number
            or not isinstance(payload.get("result"), dict)):
        raise ProbeError("MCP_PROTOCOL_UNVERIFIED")
    return payload["result"]


def _initialized(conn: http.client.HTTPConnection, pinned_sock: socket.socket,
                 secret: str) -> None:
    """Complete the MCP initialization handshake without changing the pinned socket."""
    if conn.sock is not pinned_sock or pinned_sock.fileno() < 0:
        raise ProbeError("VERIFIED_CONNECTION_CLOSED")
    body = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    conn.request("POST", "/mcp/" + secret, body=body,
                 headers={"Content-Type": "application/json",
                          "Accept": "application/json, text/event-stream"})
    response = conn.getresponse()
    # Notifications have no JSON-RPC result. The read-only adapter uses 202.
    data = response.read(1025)
    if response.status != 202 or data:
        raise ProbeError("MCP_PROTOCOL_UNVERIFIED")
    if conn.sock is not pinned_sock or pinned_sock.fileno() < 0:
        raise ProbeError("VERIFIED_CONNECTION_CLOSED")


def _recipients(value: object) -> set[tuple[int, str, str]]:
    if not isinstance(value, list):
        raise ProbeError("EFFECTIVE_AUTHORITY_UNVERIFIED")
    actual = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"target_agent_id", "expected_target_name", "kind"}
                or type(row["target_agent_id"]) is not int or row["target_agent_id"] <= 0
                or not isinstance(row["expected_target_name"], str) or row["kind"] != "to"):
            raise ProbeError("EFFECTIVE_AUTHORITY_UNVERIFIED")
        actual.append((row["target_agent_id"], row["expected_target_name"], row["kind"]))
    if len(actual) != len(set(actual)):
        raise ProbeError("EFFECTIVE_AUTHORITY_UNVERIFIED")
    return set(actual)


def validate_effective_status(status: dict, tools: list, expected: Authority) -> dict:
    """Compare effective RPC results, not persisted launchd environment values."""
    try:
        names = [item["name"] for item in tools]
        grant = status["message_authority"]
        valid = (
            all(type(name) is str for name in names)
            and len(names) == 6
            and set(names) == READ_TOOLS | MESSAGE_TOOLS
            and status["dashboard"]["runtime_mode"] == "fresh"
            and status["dashboard"]["mail_instance_id"] == expected.mail_instance_id
            and all(type(status["adapter"][flag]) is bool for flag in (
                "read_only", "spawn_enabled_on_server", "message_enabled_on_server",
            ))
            and status["adapter"] == {
                "read_only": False, "spawn_enabled_on_server": False,
                "message_enabled_on_server": True,
            }
            and grant["mail_instance_id"] == expected.mail_instance_id
            and type(grant["sender_agent_id"]) is int
            and grant["sender_agent_id"] == expected.sender_agent_id
            and grant["approval_mode"] == "operator_delegated"
            and grant["unknown_recovery"] == "authenticated_receipt_read"
            and _recipients(grant["recipients"]) == set(expected.recipients)
        )
        if not valid:
            raise ProbeError("EFFECTIVE_AUTHORITY_UNVERIFIED")
    except (KeyError, ValueError, TypeError, ProbeError):
        raise ProbeError("EFFECTIVE_AUTHORITY_UNVERIFIED") from None
    return {"verified": True, "runtime_mode": "fresh",
            "mail_instance_id": expected.mail_instance_id,
            "sender_agent_id": expected.sender_agent_id,
            "recipient_count": len(expected.recipients),
            "message_enabled_on_server": True, "spawn_enabled_on_server": False,
            "tools_verified": len(names)}


def probe(*, label: str, plist_path: str, port: int, pairing_secret: str,
          expected: Authority, runner: Callable = subprocess.run) -> dict:
    """Never disclose the pairing secret before verifying the owning PID."""
    if (type(port) is not int or not 1024 <= port <= 65535
            or not isinstance(pairing_secret, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", pairing_secret)):
        raise ProbeError("INVALID_PROBE_CONFIGURATION")
    pid = job_pid(label, plist_path, runner=runner)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.connect()
        if not _owns_connection(conn.sock, port, pid, runner):
            raise ProbeError("SERVICE_LISTENER_UNVERIFIED")
        if job_pid(label, plist_path, runner=runner) != pid:
            raise ProbeError("SERVICE_PROCESS_CHANGED")
        pinned_sock = conn.sock
        init = _request(conn, pinned_sock, pairing_secret, "initialize", 1, {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "fresh-process-probe", "version": "1"},
        })
        if (init.get("serverInfo", {}).get("name") != "orrery-chatgpt-mcp"
                or init.get("protocolVersion") != "2025-11-25"
                or not isinstance(init.get("capabilities", {}).get("tools"), dict)):
            raise ProbeError("MCP_IDENTITY_UNVERIFIED")
        _initialized(conn, pinned_sock, pairing_secret)
        tools = _request(conn, pinned_sock, pairing_secret, "tools/list", 2).get("tools")
        status = _request(conn, pinned_sock, pairing_secret, "tools/call", 3,
                          {"name": "orrery_status", "arguments": {}})
        if status.get("isError") is not False:
            raise ProbeError("MCP_STATUS_UNVERIFIED")
        effective = validate_effective_status(status["structuredContent"], tools, expected)
        # A verified socket is not proof that launchd still owns the same PID
        # after the RPC sequence. Reject a changed or vanished service.
        if job_pid(label, plist_path, runner=runner) != pid:
            raise ProbeError("SERVICE_PROCESS_CHANGED")
        return {"pid": pid, **effective}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError,
            http.client.HTTPException, subprocess.TimeoutExpired, TimeoutError):
        raise ProbeError("MCP_STATUS_UNVERIFIED") from None
    finally:
        conn.close()
