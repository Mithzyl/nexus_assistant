"""Deterministic advice math, freshness, missingness and model-output validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from assistant_api.commute.advice import (
    build_advisory_values,
    evaluate_eta,
    evaluate_weather,
    material_change,
    render_template,
    validate_model_text,
)
from assistant_api.commute.providers.base import (
    AdviceFacts,
    AdviceText,
    EtaResult,
    ForecastPeriod,
    LocationForecast,
    WeatherResult,
)
from assistant_api.models import CommutePlan

TARGET = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)


def plan() -> CommutePlan:
    return CommutePlan(
        plan_id="p1",
        user_id="u1",
        name="x",
        enabled=True,
        timezone="Asia/Shanghai",
        weekdays_json=[5],
        arrival_local_time="09:00",
        check_local_time="07:30",
        transport_mode="driving",
        buffer_minutes=10,
        rain_probability_threshold=0.4,
        rain_extra_buffer_minutes=10,
        notify_enabled=True,
        origin_label="a",
        origin_latitude=1.0,
        origin_longitude=2.0,
        destination_label="b",
        destination_latitude=3.0,
        destination_longitude=4.0,
        revision=1,
    )


def instance():
    return SimpleNamespace(
        target_arrival_at=TARGET,
        local_service_date=datetime(2026, 10, 23).date(),
    )


def periods(probability, *, start=NOW - timedelta(hours=1), hours=6, missing=False):
    values = []
    cursor = start
    for _ in range(hours):
        values.append(
            ForecastPeriod(
                start_at=cursor,
                end_at=cursor + timedelta(hours=1),
                temperature_c=20.0,
                precipitation_probability=None if missing else probability,
            )
        )
        cursor += timedelta(hours=1)
    return tuple(values)


def weather(probability=0.2, *, fetched_at=NOW, destination_periods=None, missing=False):
    fetched_at = fetched_at
    origin = LocationForecast("origin", "fake", fetched_at, periods(probability, missing=missing))
    destination = LocationForecast(
        "destination",
        "fake",
        fetched_at,
        destination_periods if destination_periods is not None else periods(probability, missing=missing),
    )
    return WeatherResult(
        provider="fake",
        fetched_at=fetched_at,
        origin=origin,
        destination=destination,
        attribution_text="t",
        attribution_url="u",
        valid_from=origin.periods[0].start_at,
        valid_to=origin.periods[-1].end_at,
    )


def eta(duration=1800, *, fetched_at=NOW):
    return EtaResult(
        provider="fake",
        transport_mode="driving",
        fetched_at=fetched_at,
        request_depart_at=TARGET - timedelta(minutes=10),
        target_arrival_at=TARGET,
        duration_seconds=duration,
        distance_meters=1,
    )


def values(weather_result=None, eta_result=None, *, now=NOW):
    w = evaluate_weather(
        weather_result if weather_result is not None else weather(),
        window_start=NOW,
        window_end=TARGET,
        now=now,
        plan=plan(),
    )
    e = evaluate_eta(eta_result if eta_result is not None else eta(), now=now)
    return build_advisory_values(
        plan=plan(),
        instance=instance(),
        now=now,
        eta=e,
        weather=w,
        previous=None,
    )


def test_missing_probability_is_unknown_not_zero():
    result = evaluate_weather(
        weather(probability=None, missing=True),
        window_start=NOW,
        window_end=TARGET,
        now=NOW,
        plan=plan(),
    )
    assert result.available is True
    assert result.quality == "degraded"
    assert result.precipitation_probability_max is None
    assert result.precipitation_probability_known is False
    assert result.rain_risk is None
    assert result.extra_buffer_seconds == 0
    assert "WEATHER_PRECIPITATION_UNKNOWN" in result.reason_codes


def test_partial_coverage_degrades_but_observed_rain_still_adds_buffer():
    origin = periods(0.9)
    result = evaluate_weather(
        weather(0.2, destination_periods=periods(0.9, start=NOW)[:1]),
        window_start=NOW,
        window_end=NOW + timedelta(hours=2),
        now=NOW,
        plan=plan(),
    )
    assert result.coverage == "partial"
    assert result.quality == "degraded"
    assert result.rain_risk is True
    assert result.extra_buffer_seconds == 600


def test_stale_weather_is_degraded_and_stale_eta_is_not_used():
    stale_weather = evaluate_weather(
        weather(fetched_at=NOW - timedelta(hours=2)),
        window_start=NOW,
        window_end=TARGET,
        now=NOW,
        plan=plan(),
    )
    assert stale_weather.quality == "degraded"
    assert "WEATHER_STALE" in stale_weather.reason_codes
    stale_eta = evaluate_eta(eta(fetched_at=NOW - timedelta(minutes=11)), now=NOW)
    assert stale_eta.available is False
    assert stale_eta.duration_seconds is None


def test_recommended_departure_subtracts_eta_buffers_and_rain():
    rainy = values(weather(0.7))
    assert rainy["eta_seconds"] == 1800
    assert rainy["weather_extra_buffer_seconds"] == 600
    assert rainy["recommended_departure_at"] == TARGET - timedelta(minutes=50)
    assert rainy["departure_status"] == "upcoming"

    late_now = TARGET - timedelta(minutes=20)
    late = build_advisory_values(
        plan=plan(),
        instance=instance(),
        now=late_now,
        eta=evaluate_eta(eta(fetched_at=late_now), now=late_now),
        weather=evaluate_weather(
            weather(fetched_at=late_now),
            window_start=NOW,
            window_end=TARGET,
            now=late_now,
            plan=plan(),
        ),
        previous=None,
    )
    assert late["departure_status"] == "past_due"
    assert late["title"] == "现在出发可能偏晚"


def test_both_missing_quality_unavailable_and_no_numbers_invented():
    w = evaluate_weather(None, window_start=NOW, window_end=TARGET, now=NOW, plan=plan())
    e = evaluate_eta(None, now=NOW)
    result = build_advisory_values(
        plan=plan(), instance=instance(), now=NOW, eta=e, weather=w, previous=None
    )
    assert result["quality"] == "unavailable"
    assert result["recommended_departure_at"] is None
    assert result["eta_seconds"] is None
    assert result["departure_status"] == "unknown"
    assert result["title"] == "通勤建议暂不可用"
    assert "无法给出可靠的出发时间" in result["summary"]


def test_material_change_requires_earlier_by_ten_minutes_or_rain_crossing():
    first = values()
    previous = SimpleNamespace(
        quality="fresh",
        recommended_departure_at=first["recommended_departure_at"],
        weather_json={"rain_risk": False},
    )
    assert material_change(previous, first) is False
    earlier = dict(first)
    earlier["recommended_departure_at"] = first["recommended_departure_at"] - timedelta(minutes=10)
    assert material_change(previous, earlier) is True
    rainier = dict(first)
    rainier["weather"] = {**first["weather"], "rain_risk": True}
    assert material_change(previous, rainier) is True


def test_model_text_may_rephrase_but_not_invent_numbers_or_alerts():
    facts = AdviceFacts(
        transport_mode="driving",
        target_arrival_local="09:00",
        recommended_departure_local="08:20",
        eta_minutes=30,
        buffer_minutes=10,
        weather_extra_buffer_minutes=0,
        temperature_c=20.0,
        rain_probability_percent=20,
        rain_risk=False,
        departure_status="upcoming",
        weather_available=True,
        eta_available=True,
    )
    fallback = render_template(facts)
    accepted = validate_model_text(
        AdviceText(title="08:20 出发", summary="驾车约 30 分钟，预计 09:00 前到达。"),
        fallback,
    )
    assert accepted is not None
    assert validate_model_text(
        AdviceText(title="08:20 出发", summary="驾车约 25 分钟即可到达。"), fallback
    ) is None
    assert validate_model_text(
        AdviceText(title="暴雨红色预警", summary="预计 09:00 到达。"), fallback
    ) is None
