from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
import pytest
import sys
from dataclasses import dataclass
from queue import Queue

from dashboard import claude_quota_observe
from dashboard.quota_server import inject_usage_ui
from dashboard.quotas import codex as codex_quota
from dashboard.quotas.antigravity import AntigravityQuotaProvider
from dashboard.quotas.base import QuotaBucket, QuotaSnapshot
from dashboard.quotas.codex import CodexQuotaProvider, _read_response
from dashboard.quotas.service import QuotaService


def _ok(provider: str) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=provider,
        source=f"{provider}-fixture",
        observed_at=1000,
        status="ok",
        buckets=(
            QuotaBucket.from_remaining(
                id="five-hour",
                label="5h",
                scope="account",
                remaining_percent=75,
                window_seconds=18000,
                resets_at=1200,
            ),
        ),
    )


def test_antigravity_opt_in_gate_never_invokes_cli():
    calls: list[object] = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("runner must not be called before explicit opt-in")

    provider = AntigravityQuotaProvider(
        command="/fake/agy",
        enabled=False,
        runner=runner,
    )
    snapshot = provider.read()

    assert snapshot.status == "unavailable"
    assert snapshot.reason == "telemetry_opt_in_required"
    assert calls == []


def test_antigravity_runtime_marker_enables_poll_without_service_env(tmp_path):
    calls: list[list[str]] = []
    marker = tmp_path / "antigravity-quota.enabled"

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "Antigravity CLI 1.1.24\n", "")
        payload = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "five-hour",
                                "displayName": "5h",
                                "remainingFraction": 0.5,
                            }
                        ],
                    }
                ]
            }
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    provider = AntigravityQuotaProvider(
        command="/fake/agy",
        opt_in_file=marker,
        runner=runner,
    )
    before = provider.read()
    assert before.status == "unavailable"
    assert before.reason == "telemetry_opt_in_required"
    assert calls == []

    marker.touch()
    after = provider.read()
    assert after.status == "ok"
    assert after.buckets[0].remaining_percent == 50.0
    assert calls == [
        [provider.command, "--version"],
        [provider.command, "-p", "/usage", "--output-format", "json"],
    ]


def test_antigravity_opted_in_adapter_uses_only_read_only_usage_command():
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "Antigravity CLI 1.1.24\n", "")
        payload = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "five-hour",
                                "displayName": "5h",
                                "remainingFraction": 0.61,
                            }
                        ],
                    }
                ]
            }
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    provider = AntigravityQuotaProvider(
        command="/fake/agy",
        enabled=True,
        runner=runner,
    )
    snapshot = provider.read()

    assert snapshot.status == "ok"
    assert snapshot.buckets[0].remaining_percent == 61.0
    assert calls == [
        [provider.command, "--version"],
        [provider.command, "-p", "/usage", "--output-format", "json"],
    ]


def test_codex_adapter_accepts_injected_transport_without_spawning_process():
    calls: list[tuple[str, float]] = []

    def reader(command: str, *, timeout: float):
        calls.append((command, timeout))
        return {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 20,
                    "windowDurationMins": 300,
                    "resetsAt": 2000,
                },
            }
        }

    provider = CodexQuotaProvider(
        command="codex-fixture",
        timeout=4.25,
        reader=reader,
    )
    snapshot = provider.read()

    assert calls == [("codex-fixture", 4.25)]
    assert snapshot.status == "ok"
    assert snapshot.buckets[0].label == "5h"
    assert snapshot.buckets[0].remaining_percent == 80.0


def test_codex_response_reader_is_pipe_independent():
    responses: Queue[str | None] = Queue()
    responses.put('{"method":"account/rateLimits/updated"}\n')
    responses.put('{"id":2,"result":{"rateLimits":{}}}\n')

    result = _read_response(responses, 2, time.monotonic() + 1)

    assert result == {"rateLimits": {}}


def test_codex_transport_matches_documented_headerless_jsonl(monkeypatch):
    class RecordingStdin:
        def __init__(self) -> None:
            self.chunks: list[str] = []

        def write(self, value: str) -> int:
            self.chunks.append(value)
            return len(value)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = RecordingStdin()
            self.stdout = io.StringIO(
                '{"id":1,"result":{"userAgent":"test"}}\n'
                '{"id":2,"result":{"rateLimits":{"limitId":"codex","primary":'
                '{"usedPercent":20,"windowDurationMins":300,"resetsAt":2000}}}}\n'
            )
            self.returncode: int | None = None

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def wait(self, timeout=None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    process = FakeProcess()
    monkeypatch.setattr(codex_quota.subprocess, "Popen", lambda *args, **kwargs: process)

    result = codex_quota._read_app_server_rate_limits("codex-fixture", timeout=1)
    wire = [json.loads(line) for line in "".join(process.stdin.chunks).splitlines()]

    assert result["rateLimits"]["primary"]["windowDurationMins"] == 300
    assert [message["method"] for message in wire] == [
        "initialize",
        "initialized",
        "account/rateLimits/read",
    ]
    assert all("jsonrpc" not in message for message in wire)


def test_claude_observer_concurrent_writes_are_atomic(tmp_path, monkeypatch):
    target = tmp_path / "claude-quota.json"
    monkeypatch.setenv("AGENTSTACK_CLAUDE_QUOTA_SNAPSHOT", str(target))
    errors: list[BaseException] = []

    def writer(used: int) -> None:
        try:
            claude_quota_observe._write_snapshot(
                {"five_hour": {"used_percentage": used, "resets_at": 2000}}
            )
        except BaseException as exc:  # test records cross-thread failures
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(used,)) for used in range(10, 60, 10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["rate_limits"]["five_hour"]["used_percentage"] in {10, 20, 30, 40, 50}
    assert list(tmp_path.glob(".claude-quota.json.*.tmp")) == []
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


@dataclass
class _BlockingProvider:
    provider_name: str
    entered: set[str]
    condition: threading.Condition
    release: threading.Event
    source_name: str = "fixture"
    ttl_seconds: int = 1

    def read(self) -> QuotaSnapshot:
        with self.condition:
            self.entered.add(self.provider_name)
            self.condition.notify_all()
        if not self.release.wait(timeout=3):
            raise RuntimeError("test release timeout")
        return _ok(self.provider_name)


def test_quota_service_refreshes_independent_providers_concurrently():
    entered: set[str] = set()
    condition = threading.Condition()
    release = threading.Event()
    providers = [
        _BlockingProvider("claude", entered, condition, release),
        _BlockingProvider("codex", entered, condition, release),
    ]
    service = QuotaService(providers, clock=lambda: 1000.0)
    result: list[dict[str, object]] = []

    worker = threading.Thread(target=lambda: result.append(service.read_all()), daemon=True)
    worker.start()
    try:
        with condition:
            both_started = condition.wait_for(lambda: len(entered) == 2, timeout=1.5)
        assert both_started, "provider refreshes were serialized behind one global lock"
    finally:
        release.set()
        worker.join(timeout=4)

    assert not worker.is_alive()
    assert [item["status"] for item in result[0]["providers"]] == ["ok", "ok"]


def test_provider_exception_details_do_not_escape_api_or_logs(caplog):
    class FailingProvider:
        provider_name = "codex"
        source_name = "fixture"
        ttl_seconds = 1

        def read(self) -> QuotaSnapshot:
            raise RuntimeError("secret-account-token-should-not-leak")

    payload = QuotaService([FailingProvider()], clock=lambda: 1000.0).read_all()
    provider = payload["providers"][0]

    assert provider["status"] == "unavailable"
    assert provider["reason"] == "provider_read_failed"
    assert "secret-account-token" not in json.dumps(payload)
    assert "secret-account-token" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_demo_usage_strip_never_falls_through_to_live_quota():
    source = b'<html><head></head><body><main id="wrap"></main></body></html>'
    injected = inject_usage_ui(source)

    assert b"demo-fixture" in injected
    assert b"params.get('demo')==='1'" in injected
    assert b"if(demo){renderQuota(demoQuota);return;}" in injected
    assert b"Number.isFinite" in injected
    assert b"usage-observed" in injected
    assert b"usage-status" in injected
    assert b"status!=='ok'" in injected


@pytest.mark.parametrize("code,stdout,stderr", [
    (1, "1.2.1", "failed"), (0, "", "requires version 1.2.1"),
    (0, "Gemini CLI 2.0.0", ""), (0, "1.1.11-beta.1", ""),
    (0, "1.1.10", ""), (0, "1.1.11", ""), (0, "1.1.23", ""),
])
def test_antigravity_version_gate_rejects_unproven_read_only_support(code, stdout, stderr):
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, code, stdout, stderr)
    result = AntigravityQuotaProvider(command="fixture", enabled=True, runner=runner).read()
    assert result.status == "unavailable"
    assert len(calls) == 1
    assert calls[0][0][-1] == "--version"
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert calls[0][1]["encoding"] == "utf-8"


def test_concurrent_poll_does_not_wait_or_launch_duplicate_provider():
    entered = set()
    condition = threading.Condition()
    release = threading.Event()
    provider = _BlockingProvider("codex", entered, condition, release)
    service = QuotaService([provider], clock=lambda: 1000.0)
    worker = threading.Thread(target=service.read_all, daemon=True)
    worker.start()
    try:
        with condition:
            assert condition.wait_for(lambda: bool(entered), timeout=1)
        # No release is signalled: a locking implementation would wait until
        # the fake provider's timeout rather than returning in-flight status.
        result = service.read_all()["providers"][0]
        assert result["status"] == "unavailable"
        assert result["reason"] == "refresh_in_progress"
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(timeout=4)
    assert not worker.is_alive()


def test_response_rechecks_age_after_another_provider_finishes():
    now = [1000.0]
    class SlowProvider:
        provider_name = "codex"
        source_name = "fixture"
        ttl_seconds = 60
        def read(self):
            now[0] = 1012.0
            return _ok("codex")
    result = QuotaService([SlowProvider()], clock=lambda: now[0], stale_seconds=10).read_all()
    assert result["ts"] == 1012
    assert result["providers"][0]["status"] == "unavailable"
    assert result["providers"][0]["buckets"] == []


def test_codex_silent_real_subprocess_times_out_and_is_reaped(monkeypatch):
    native_popen = subprocess.Popen
    children = []
    def popen(argv, **kwargs):
        child = native_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(codex_quota.subprocess, "Popen", popen)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        codex_quota._read_app_server_rate_limits("fake-codex", timeout=0.2)
    assert time.monotonic() - started < 5
    assert children[0].poll() is not None
    assert children[0].stdin.closed
    assert children[0].stdout.closed


def test_codex_invalid_utf8_is_not_logged_from_reader_thread():
    responses = Queue()
    stream = io.TextIOWrapper(io.BytesIO(b"secret-account\xff\n"), encoding="utf-8")
    codex_quota._pump_stdout(stream, responses)
    assert responses.get_nowait() is None


@pytest.mark.parametrize("value", [True, False, 1.5, "300", float("inf"), -1, 0])
def test_codex_invalid_duration_does_not_manufacture_window(value):
    snapshot = codex_quota.parse_codex_rate_limits(
        {"rateLimits": {"primary": {"windowDurationMins": value, "usedPercent": 20}}},
        observed_at=1000,
    )
    assert snapshot.status == "unavailable"
    assert snapshot.buckets == ()


@pytest.mark.parametrize("value", [True, False, 1200.5, "1200", float("inf"), -1])
def test_codex_invalid_reset_does_not_manufacture_timestamp(value):
    snapshot = codex_quota.parse_codex_rate_limits(
        {"rateLimits": {"primary": {
            "windowDurationMins": 300, "usedPercent": 20, "resetsAt": value,
        }}},
        observed_at=1000,
    )
    assert snapshot.buckets[0].resets_at is None
