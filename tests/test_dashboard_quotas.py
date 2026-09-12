from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from dashboard.quota_server import _USAGE_HTML, inject_usage_ui
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
    # Provider labels are payload-derived (including demo fixtures); only the
    # static markup must remain free of hardcoded provider labels.
    assert "Gemini Models" not in _USAGE_HTML


def test_normal_cache_cannot_extend_claude_observation_lifetime(tmp_path):
    path = tmp_path / "claude.json"
    path.write_text(json.dumps({"observed_at": 1000, "rate_limits": {
        "five_hour": {"used_percentage": 25, "resets_at": 2000},
    }}), encoding="utf-8")
    now = [1599.0]
    provider = ClaudeQuotaProvider(path, clock=lambda: now[0])
    service = QuotaService([provider], clock=lambda: now[0])
    assert service.read_all()["providers"][0]["status"] == "ok"
    now[0] = 1601.0  # still in the 30-second cache TTL, but observation expired
    expired = service.read_all()["providers"][0]
    assert expired["status"] == "unavailable"
    assert expired["buckets"] == []


def test_service_marks_pre_reset_value_stale_without_inventing_new_balance():
    now = [1000.0]
    provider = _Provider("codex", "codex-source", _ok_snapshot("codex", 1000), ttl_seconds=180)
    service = QuotaService([provider], clock=lambda: now[0])
    assert service.read_all()["providers"][0]["status"] == "ok"
    now[0] = 1101.0
    snapshot = service.read_all()["providers"][0]
    assert snapshot["status"] == "stale"
    assert snapshot["reason"] == "window_reset_pending"
    assert snapshot["buckets"][0]["remaining_percent"] == 75
    assert provider.calls == 1


def test_future_observation_is_not_accepted_as_fresh(tmp_path):
    path = tmp_path / "claude.json"
    path.write_text(json.dumps({"observed_at": 2000, "rate_limits": {
        "five_hour": {"used_percentage": 25},
    }}), encoding="utf-8")
    assert ClaudeQuotaProvider(path, clock=lambda: 1000).read().status == "unavailable"
    provider = _Provider("codex", "fixture", _ok_snapshot("codex", 2000))
    assert QuotaService([provider], clock=lambda: 1000).read_all()["providers"][0]["buckets"] == []


def test_codex_keyed_limits_override_legacy_view_and_keep_distinct_labels():
    def record(used, name=None):
        return {"limitName": name, "primary": {
            "usedPercent": used, "windowDurationMins": 300, "resetsAt": 2000,
        }}
    snapshot = parse_codex_rate_limits({
        "rateLimits": {"limitId": "codex", **record(10)},
        "rateLimitsByLimitId": {"codex": record(20), "other": record(80, "Other pool")},
    }, observed_at=1000)
    assert len(snapshot.buckets) == 2
    assert [b.remaining_percent for b in snapshot.buckets] == [80, 20]
    assert [b.label for b in snapshot.buckets] == ["codex · 5h", "Other pool · 5h"]


def test_codex_groups_windows_by_usage_pool_before_sorting_by_duration():
    def record(name, durations):
        windows = [
            {"usedPercent": index, "windowDurationMins": duration, "resetsAt": 2000 + index}
            for index, duration in enumerate(durations, start=10)
        ]
        return {"limitName": name, "primary": windows[0], "secondary": windows[1]}

    snapshot = parse_codex_rate_limits(
        {
            "rateLimitsByLimitId": {
                "spark": record("GPT-5.3-Codex-Spark", [10080, 300]),
                "codex": record("codex", [10080, 300]),
            }
        },
        observed_at=1000,
    )

    assert [bucket.label for bucket in snapshot.buckets] == [
        "codex · 5h",
        "codex · 7d",
        "GPT-5.3-Codex-Spark · 5h",
        "GPT-5.3-Codex-Spark · 7d",
    ]


def test_codex_pool_order_uses_shortest_window_not_map_order():
    def record(name, windows):
        return {"limitName": name, **windows}

    codex = record("codex", {
        "primary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 3000},
    })
    spark = record("GPT-5.3-Codex-Spark", {
        "primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": 1500},
        "secondary": {"usedPercent": 30, "windowDurationMins": 10080, "resetsAt": 3000},
    })
    expected = [
        "GPT-5.3-Codex-Spark · 5h",
        "GPT-5.3-Codex-Spark · 7d",
        "codex · 7d",
    ]

    for limits in ({"codex": codex, "spark": spark}, {"spark": spark, "codex": codex}):
        snapshot = parse_codex_rate_limits(
            {"rateLimitsByLimitId": limits},
            observed_at=1000,
        )
        assert [bucket.label for bucket in snapshot.buckets] == expected


@pytest.mark.parametrize("fraction", [1.01, 58, -0.01, True, float("nan"), float("inf")])
def test_antigravity_rejects_invalid_fractions_without_guessing_units(fraction):
    snapshot = parse_antigravity_usage({"groups": [{"buckets": [
        {"id": "invalid", "remainingFraction": fraction},
        {"id": "valid", "remainingFraction": 0},
    ]}]}, observed_at=1000)
    assert [b.id for b in snapshot.buckets] == ["valid"]
    assert snapshot.buckets[0].remaining_percent == 0


def test_antigravity_uses_explicit_window_and_does_not_parse_model_id_as_duration():
    snapshot = parse_antigravity_usage({"groups": [{"buckets": [
        {"id": "gemini-3model", "displayName": "Model", "remainingFraction": 0.5},
        {"id": "opaque", "displayName": "Limit", "window": "5h", "remainingFraction": 1},
        {"id": "disabled", "remainingFraction": 1, "disabled": True},
    ]}]}, observed_at=1000)
    assert [b.window_seconds for b in snapshot.buckets] == [None, 18000]


@pytest.mark.parametrize("field,value", [
    ("used_percent", True), ("used_percent", float("nan")),
    ("remaining_percent", 50), ("window_seconds", float("inf")),
    ("window_seconds", True), ("resets_at", float("nan")), ("id", 1),
])
def test_bucket_schema_rejects_invalid_or_contradictory_data(field, value):
    values = dict(id="quota", label="5h", scope="account", used_percent=25,
                  remaining_percent=75, window_seconds=18000, resets_at=2000)
    values[field] = value
    with pytest.raises(ValueError):
        QuotaBucket(**values)
