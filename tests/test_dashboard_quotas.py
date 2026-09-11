from __future__ import annotations

from dataclasses import dataclass

from dashboard.quota_server import inject_usage_ui
from dashboard.quotas.antigravity import parse_antigravity_usage
from dashboard.quotas.base import QuotaBucket, QuotaSnapshot
from dashboard.quotas.claude import ClaudeQuotaProvider, parse_claude_statusline
from dashboard.quotas.codex import parse_codex_rate_limits
from dashboard.quotas.service import QuotaService


def test_claude_statusline_normalizes_subscription_windows():
    snapshot = parse_claude_statusline(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": 23.5, "resets_at": 1000},
                "seven_day": {"used_percentage": 41.2, "resets_at": 2000},
            }
        },
        observed_at=900,
    )

    assert snapshot.status == "ok"
    assert [bucket.label for bucket in snapshot.buckets] == ["5h", "7d"]
    assert snapshot.buckets[0].remaining_percent == 76.5
    assert snapshot.buckets[1].remaining_percent == 58.8


def test_claude_provider_expires_old_observation(tmp_path):
    path = tmp_path / "claude-quota.json"
    path.write_text(
        '{"observed_at":1000,"rate_limits":{"five_hour":{"used_percentage":25,"resets_at":2000}}}',
        encoding="utf-8",
    )
    now = [1005.0]
    provider = ClaudeQuotaProvider(path, max_age_seconds=10, clock=lambda: now[0])

    assert provider.read().status == "ok"
    now[0] = 1011.0
    expired = provider.read()
    assert expired.status == "unavailable"
    assert expired.reason == "observation_stale"
    assert expired.buckets == ()
    assert expired.observed_at == 1000


def test_codex_uses_window_duration_not_primary_secondary_position():
    snapshot = parse_codex_rate_limits(
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 31,
                    "windowDurationMins": 10080,
                    "resetsAt": 3000,
                },
                "secondary": {
                    "usedPercent": 20,
                    "windowDurationMins": 300,
                    "resetsAt": 1500,
                },
            }
        },
        observed_at=1000,
    )

    assert [bucket.label for bucket in snapshot.buckets] == ["5h", "7d"]
    assert [bucket.remaining_percent for bucket in snapshot.buckets] == [80.0, 69.0]


def test_codex_does_not_invent_a_missing_five_hour_window():
    snapshot = parse_codex_rate_limits(
        {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 31,
                    "windowDurationMins": 10080,
                    "resetsAt": 3000,
                },
                "secondary": None,
            }
        },
        observed_at=1000,
    )

    assert [bucket.label for bucket in snapshot.buckets] == ["7d"]


def test_antigravity_uses_only_returned_dynamic_buckets():
    snapshot = parse_antigravity_usage(
        {
            "command": {
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {
                                    "id": "gemini-5h",
                                    "window": "5h",
                                    "remaining_fraction": 0.82,
                                    "reset_time": "2026-09-11T18:00:00Z",
                                },
                                {
                                    "id": "disabled",
                                    "window": "weekly",
                                },
                            ],
                        },
                        {
                            "name": "Claude and GPT models",
                            "buckets": [
                                {
                                    "id": "3p-weekly",
                                    "window": "weekly",
                                    "remaining_fraction": 0.58,
                                    "reset_time": "2026-09-14T00:00:00Z",
                                }
                            ],
                        },
                    ]
                }
            }
        },
        observed_at=1000,
    )

    assert snapshot.status == "ok"
    assert [bucket.id for bucket in snapshot.buckets] == ["gemini-5h", "3p-weekly"]
    assert snapshot.buckets[0].remaining_percent == 82.0
    assert snapshot.buckets[1].remaining_percent == 58.0


def test_antigravity_accepts_current_camelcase_summary_shape():
    snapshot = parse_antigravity_usage(
        {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "displayName": "Five Hour Limit",
                                "remainingFraction": 0.41,
                                "resetTime": "2026-09-11T18:00:00Z",
                            },
                            {
                                "bucketId": "gemini-weekly",
                                "displayName": "Weekly Limit",
                                "remainingFraction": 0.73,
                                "resetTime": "2026-09-14T00:00:00Z",
                            },
                        ],
                    }
                ]
            }
        },
        observed_at=1000,
    )

    assert snapshot.status == "ok"
    assert [bucket.id for bucket in snapshot.buckets] == ["gemini-5h", "gemini-weekly"]
    assert [bucket.label for bucket in snapshot.buckets] == [
        "Gemini Models · 5h",
        "Gemini Models · 7d",
    ]
    assert [bucket.remaining_percent for bucket in snapshot.buckets] == [41.0, 73.0]


@dataclass
class _Provider:
    provider_name: str
    source_name: str
    result: QuotaSnapshot | Exception
    ttl_seconds: int = 60
    calls: int = 0

    def read(self) -> QuotaSnapshot:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _ok_snapshot(provider: str, observed_at: int) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=provider,
        source=f"{provider}-source",
        observed_at=observed_at,
        status="ok",
        buckets=(
            QuotaBucket.from_remaining(
                id="5h",
                label="5h",
                scope="account",
                remaining_percent=75,
                window_seconds=18000,
                resets_at=observed_at + 100,
            ),
        ),
    )


def test_service_isolates_provider_failure_and_honors_ttl():
    now = [1000.0]
    good = _Provider("good", "good-source", _ok_snapshot("good", 1000))
    bad = _Provider("bad", "bad-source", RuntimeError("offline"))
    service = QuotaService([good, bad], clock=lambda: now[0])

    first = service.read_all()
    second = service.read_all()

    assert first["degraded"] is True
    assert [item["status"] for item in first["providers"]] == ["ok", "unavailable"]
    assert good.calls == 1
    assert bad.calls == 1
    assert second == first


def test_service_uses_recent_success_as_bounded_stale_value():
    now = [1000.0]
    provider = _Provider("codex", "codex-source", _ok_snapshot("codex", 1000), ttl_seconds=1)
    service = QuotaService([provider], stale_seconds=10, clock=lambda: now[0])
    assert service.read_all()["providers"][0]["status"] == "ok"

    provider.result = RuntimeError("temporary failure")
    now[0] = 1002.0
    stale = service.read_all()["providers"][0]
    assert stale["status"] == "stale"
    assert stale["buckets"][0]["remaining_percent"] == 75.0

    now[0] = 1012.0
    unavailable = service.read_all()["providers"][0]
    assert unavailable["status"] == "unavailable"
    assert unavailable["buckets"] == []


def test_usage_ui_is_injected_once_and_renders_dynamic_provider_data():
    source = b"<html><head></head><body><main id=\"wrap\"></main></body></html>"
    first = inject_usage_ui(source)
    second = inject_usage_ui(first)

    assert first == second
    assert b'id="usage-strip"' in first
    assert b'body[data-view="net"] .usage-strip{display:none}' in first
    assert b"/api/quotas" in first
    assert b"data.providers" in first
    assert b"Gemini Models" not in first
