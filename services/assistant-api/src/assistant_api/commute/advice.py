"""Deterministic facts first, optional wording second.

ETA subtraction, weather-window selection and rain-threshold logic live here and
are pure functions. A language model may only replace title/summary after the
facts exist; its output is rejected if it introduces any number absent from the
deterministic template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from assistant_api.commute import constants
from assistant_api.commute.providers.base import (
    AdviceFacts,
    AdviceText,
    EtaResult,
    ForecastPeriod,
    WeatherResult,
)
from assistant_api.commute.timeutil import ensure_aware, format_minutes, utc_iso
from assistant_api.models import CommuteAdvisory, CommutePlan

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
CLOCK_RE = re.compile(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b")
UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(分钟|小时|%|°C|公里|米|秒)")


@dataclass
class WeatherEvaluation:
    available: bool
    quality: str  # fresh | degraded | unavailable
    coverage: str  # full | partial | unavailable
    fetched_at: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    temperature_c: float | None
    precipitation_probability_max: float | None
    precipitation_probability_known: bool
    rain_risk: bool | None
    extra_buffer_seconds: int
    provider: str = "unavailable"
    locations: list[dict[str, Any]] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    summary: dict[str, Any] | None = None


@dataclass
class EtaEvaluation:
    available: bool
    duration_seconds: int | None
    freshness: str
    fetched_at: datetime | None
    provider: str = "unavailable"
    request_depart_at: datetime | None = None
    target_arrival_at: datetime | None = None
    distance_meters: int | None = None
    response_basis: str | None = None
    reason_codes: list[str] = field(default_factory=list)


def _overlaps(period: ForecastPeriod, start: datetime, end: datetime) -> bool:
    return period.start_at < end and period.end_at > start


def _merge_coverage(
    periods: list[ForecastPeriod], start: datetime, end: datetime
) -> tuple[float, bool]:
    """Return covered seconds and whether every expected minute is present."""

    intervals: list[tuple[datetime, datetime]] = []
    for period in periods:
        if _overlaps(period, start, end):
            intervals.append((max(period.start_at, start), min(period.end_at, end)))
    if not intervals:
        return 0.0, False
    intervals.sort()
    covered_start, covered_end = intervals[0]
    merged: list[tuple[datetime, datetime]] = []
    for left, right in intervals[1:]:
        if left <= covered_end:
            covered_end = max(covered_end, right)
        else:
            merged.append((covered_start, covered_end))
            covered_start, covered_end = left, right
    merged.append((covered_start, covered_end))
    covered = sum((right - left).total_seconds() for left, right in merged)
    expected = max(0.0, (end - start).total_seconds())
    # Hourly provider buckets align to the hour; a one-minute tolerance avoids
    # declaring a complete forecast partial because of rounding at the edges.
    return covered, covered + 60 >= expected


def _evaluate_location(
    periods: tuple[ForecastPeriod, ...], start: datetime, end: datetime
) -> dict[str, Any]:
    overlapping = [period for period in periods if _overlaps(period, start, end)]
    probabilities = [
        period.precipitation_probability
        for period in overlapping
        if period.precipitation_probability is not None
    ]
    temperatures = [
        period.temperature_c
        for period in overlapping
        if period.temperature_c is not None
    ]
    covered, full = _merge_coverage(list(periods), start, end)
    missing = any(
        period.precipitation_probability is None for period in overlapping
    )
    return {
        "coverage": "full" if full else ("partial" if overlapping else "unavailable"),
        "covered_seconds": int(covered),
        "max_probability": max(probabilities) if probabilities else None,
        "mean_temperature": round(sum(temperatures) / len(temperatures), 1)
        if temperatures
        else None,
        "probability_periods_present": bool(probabilities),
        "missing_probability": missing,
    }


def evaluate_weather(
    result: WeatherResult | None,
    *,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    plan: CommutePlan,
    freshness_seconds: int = constants.WEATHER_FRESHNESS_SECONDS,
) -> WeatherEvaluation:
    """Select overlapping periods and decide rain risk without zero-filling."""

    window_start = ensure_aware(window_start)
    window_end = ensure_aware(window_end)
    now = ensure_aware(now)
    codes: list[str] = []
    if result is None:
        return WeatherEvaluation(
            available=False,
            quality="unavailable",
            provider="unavailable",
            coverage="unavailable",
            fetched_at=None,
            valid_from=None,
            valid_to=None,
            temperature_c=None,
            precipitation_probability_max=None,
            precipitation_probability_known=False,
            rain_risk=None,
            extra_buffer_seconds=0,
            reason_codes=["WEATHER_UNAVAILABLE"],
        )

    fetched_at = ensure_aware(result.fetched_at)
    stale = fetched_at < now - timedelta(seconds=freshness_seconds) or fetched_at > now + timedelta(minutes=5)
    if stale:
        codes.append("WEATHER_STALE")
    origin = _evaluate_location(result.origin.periods, window_start, window_end)
    destination = _evaluate_location(result.destination.periods, window_start, window_end)
    locations = [
        {"role": "origin", **origin},
        {"role": "destination", **destination},
    ]
    has_data = any(loc["coverage"] != "unavailable" for loc in locations)
    coverage_values = [loc["coverage"] for loc in locations]
    coverage = (
        "full"
        if coverage_values == ["full", "full"]
        else ("partial" if has_data else "unavailable")
    )
    missing = any(loc["missing_probability"] for loc in locations)
    full_coverage = coverage == "full"
    known = full_coverage and not missing and has_data
    probabilities = [
        loc["max_probability"] for loc in locations if loc["max_probability"] is not None
    ]
    max_probability = max(probabilities) if probabilities else None
    temperatures = [
        loc["mean_temperature"] for loc in locations if loc["mean_temperature"] is not None
    ]
    temperature = round(sum(temperatures) / len(temperatures), 1) if temperatures else None

    if not has_data:
        quality = "unavailable"
        available = False
        rain_risk: bool | None = None
        codes.append("WEATHER_UNAVAILABLE")
    else:
        available = True
        degraded = stale or not known
        quality = "degraded" if degraded else "fresh"
        if stale:
            codes.append("WEATHER_STALE")
        if not full_coverage:
            codes.append("WEATHER_COVERAGE_PARTIAL")
        if missing:
            codes.append("WEATHER_PRECIPITATION_UNKNOWN")
        if max_probability is None:
            rain_risk = None
        elif max_probability >= plan.rain_probability_threshold:
            # Even a partial forecast with an observed high probability is a
            # real reason to add rain buffer; it is never treated as dry.
            rain_risk = True
            codes.append("WEATHER_RAIN_THRESHOLD_MET")
        elif known:
            rain_risk = False
            codes.append("WEATHER_RAIN_THRESHOLD_NOT_MET")
        else:
            rain_risk = None
        if quality == "fresh":
            codes.insert(0, "WEATHER_FRESH")
        else:
            codes.insert(0, "WEATHER_DEGRADED")

    extra_seconds = (
        plan.rain_extra_buffer_minutes * 60 if rain_risk is True else 0
    )
    summary = {
        "forecast_start": utc_iso(window_start),
        "forecast_end": utc_iso(window_end),
        "temperature_c": temperature,
        "precipitation_probability_max": max_probability,
        "precipitation_probability_known": known,
        "rain_probability_threshold": plan.rain_probability_threshold,
        "rain_risk": rain_risk,
        "coverage": coverage,
        "locations": [
            {
                "role": item["role"],
                "coverage": item["coverage"],
                "max_probability": item["max_probability"],
            }
            for item in locations
        ],
    }
    return WeatherEvaluation(
        available=available,
        quality=quality,
        provider=result.provider,
        coverage=coverage,
        fetched_at=fetched_at,
        valid_from=ensure_aware(result.valid_from),
        valid_to=ensure_aware(result.valid_to),
        temperature_c=temperature,
        precipitation_probability_max=max_probability,
        precipitation_probability_known=known,
        rain_risk=rain_risk,
        extra_buffer_seconds=extra_seconds,
        locations=locations,
        reason_codes=codes,
        summary=summary,
    )


def evaluate_eta(
    result: EtaResult | None,
    *,
    now: datetime,
    freshness_seconds: int = constants.ETA_FRESHNESS_SECONDS,
) -> EtaEvaluation:
    """Reject stale or nonsensical durations instead of presenting them as live."""

    now = ensure_aware(now)
    if result is None:
        return EtaEvaluation(
            available=False,
            duration_seconds=None,
            freshness="unavailable",
            fetched_at=None,
            reason_codes=["ROUTE_UNAVAILABLE"],
        )
    fetched_at = ensure_aware(result.fetched_at)
    stale = fetched_at < now - timedelta(seconds=freshness_seconds) or fetched_at > now + timedelta(minutes=5)
    duration = result.duration_seconds
    if stale or duration is None or duration <= 0:
        return EtaEvaluation(
            available=False,
            duration_seconds=None,
            freshness="stale" if stale else "unavailable",
            fetched_at=fetched_at,
            provider=result.provider,
            request_depart_at=ensure_aware(result.request_depart_at),
            target_arrival_at=ensure_aware(result.target_arrival_at),
            distance_meters=result.distance_meters,
            response_basis=result.response_basis,
            reason_codes=["ROUTE_STALE" if stale else "ROUTE_UNAVAILABLE"],
        )
    return EtaEvaluation(
        available=True,
        duration_seconds=duration,
        freshness="fresh",
        fetched_at=fetched_at,
        provider=result.provider,
        request_depart_at=ensure_aware(result.request_depart_at),
        target_arrival_at=ensure_aware(result.target_arrival_at),
        distance_meters=result.distance_meters,
        response_basis=result.response_basis,
        reason_codes=["ROUTE_FRESH"],
    )


def departure_status(
    recommended_departure_at: datetime | None, now: datetime
) -> str:
    if recommended_departure_at is None:
        return "unknown"
    now = ensure_aware(now)
    recommended = ensure_aware(recommended_departure_at)
    window = timedelta(minutes=constants.DEPARTURE_NOW_WINDOW_MINUTES)
    if now < recommended - window:
        return "upcoming"
    if now <= recommended + window:
        return "leave_now"
    return "past_due"


def weather_sources(
    weather: WeatherEvaluation, provider: str | None
) -> dict[str, Any]:
    return {
        "kind": "weather",
        "provider": provider or "unavailable",
        "fetched_at": utc_iso(weather.fetched_at),
        "valid_from": utc_iso(weather.valid_from),
        "valid_to": utc_iso(weather.valid_to),
        "freshness": (
            "stale"
            if "WEATHER_STALE" in weather.reason_codes
            else ("fresh" if weather.fetched_at else "unavailable")
        ),
        "attribution": {
            "text": (
                constants.WEATHER_ATTRIBUTION_TEXT if provider == "weatherkit"
                else "天气数据由 Open-Meteo 提供" if provider == "open_meteo"
                else "开发/测试天气数据，非 Apple Weather"
            ),
            "url": (
                constants.WEATHER_ATTRIBUTION_URL if provider == "weatherkit"
                else "https://open-meteo.com/" if provider == "open_meteo"
                else "https://example.invalid/mock-weather"
            ),
        },
        "note": "按出发地/目的地相关预报时段估算，不代表整条道路逐点天气。",
    }


def route_sources(eta: EtaEvaluation) -> dict[str, Any]:
    return {
        "kind": "route",
        "provider": eta.provider,
        "fetched_at": utc_iso(eta.fetched_at),
        "valid_from": None,
        "valid_to": utc_iso(eta.target_arrival_at) if eta.available else None,
        "freshness": "fresh" if eta.available else eta.freshness,
        "attribution": {
            "text": constants.ROUTE_ATTRIBUTION_TEXT
            if eta.provider == "apple_maps_server_api"
            else ("高德地图路线数据" if eta.provider == "amap" else "暂无可用路线" if eta.provider == "unavailable" else "开发/测试路线数据"),
            "url": constants.ROUTE_ATTRIBUTION_URL
            if eta.provider == "apple_maps_server_api"
            else ("https://lbs.amap.com/" if eta.provider == "amap" else "" if eta.provider == "unavailable" else "https://example.invalid/mock-route"),
        },
        "request_depart_at": utc_iso(eta.request_depart_at),
        "target_arrival_at": utc_iso(eta.target_arrival_at),
        "note": "ETA 请求使用计划中的未来时间基准，不是无条件使用“现在出发”。",
    }


def _local_hhmm(value: datetime, timezone: str) -> str:
    from zoneinfo import ZoneInfo

    return ensure_aware(value).astimezone(ZoneInfo(timezone)).strftime("%H:%M")


def build_facts(
    *,
    plan: CommutePlan,
    instance,
    now: datetime,
    eta: EtaEvaluation,
    weather: WeatherEvaluation,
    recommended_departure_at: datetime | None,
    status: str,
) -> AdviceFacts:
    return AdviceFacts(
        transport_mode=plan.transport_mode,  # type: ignore[arg-type]
        target_arrival_local=_local_hhmm(instance.target_arrival_at, plan.timezone),
        recommended_departure_local=_local_hhmm(recommended_departure_at, plan.timezone)
        if recommended_departure_at
        else None,
        eta_minutes=(
            int(round(eta.duration_seconds / 60)) if eta.duration_seconds else None
        ),
        buffer_minutes=plan.buffer_minutes,
        weather_extra_buffer_minutes=(
            weather.extra_buffer_seconds // 60 if weather.extra_buffer_seconds else 0
        ),
        temperature_c=weather.temperature_c,
        rain_probability_percent=(
            round(weather.precipitation_probability_max * 100)
            if weather.precipitation_probability_max is not None
            else None
        ),
        rain_risk=weather.rain_risk,
        departure_status=status,  # type: ignore[arg-type]
        weather_available=weather.available and weather.coverage == "full",
        eta_available=eta.available,
        reason_codes=list(eta.reason_codes + weather.reason_codes),
    )


def render_template(facts: AdviceFacts) -> AdviceText:
    """Produce a safe fallback whose numbers are the only numbers allowed later."""

    mode = {"driving": "驾车", "transit": "公交", "walking": "步行"}.get(facts.transport_mode, "出行")
    if not facts.eta_available and not facts.weather_available:
        return AdviceText(
            title="通勤建议暂不可用",
            summary="天气和路线数据暂时都不可用，无法给出可靠的出发时间。请以实际路况为准，稍后可在 App 中手动刷新。",
        )
    if not facts.eta_available:
        return AdviceText(
            title="路况数据暂不可用",
            summary=(
                f"暂时无法获取{mode}耗时，因此不生成精确出发时间。"
                f"目标到达时间为 {facts.target_arrival_local}，天气数据"
                f"{'已覆盖分析时段' if facts.weather_available else '未完整覆盖分析时段，不能据此判断目标时段天气'}。"
            ),
        )
    eta = facts.eta_minutes or 0
    base = (
        f"{mode}约 {eta} 分钟，基础预留 {facts.buffer_minutes} 分钟"
        f"，计划于 {facts.target_arrival_local} 前到达"
    )
    rain_clause = ""
    if facts.rain_risk is True:
        rain_clause = (
            f"；降雨概率最高约 {facts.rain_probability_percent}%，"
            f"已加入 {facts.weather_extra_buffer_minutes} 分钟雨天预留"
        )
    elif not facts.weather_available:
        rain_clause = "；天气暂不可用，无法判断降雨，不能推断无雨"
    elif facts.rain_probability_percent is not None:
        rain_clause = f"；降雨概率最高约 {facts.rain_probability_percent}%"

    if facts.recommended_departure_local is None:
        return AdviceText(
            title="按路况规划出发时间",
            summary=base + rain_clause + "。",
        )
    if facts.departure_status == "past_due":
        title = "现在出发可能偏晚"
        summary = (
            f"建议出发时间 {facts.recommended_departure_local} 已过，"
            + base
            + rain_clause
            + "。请尽快出发。"
        )
    elif facts.departure_status == "leave_now":
        title = "现在出发"
        summary = (
            f"建议现在出发，{base}{rain_clause}。"
        )
    else:
        title = f"建议 {facts.recommended_departure_local} 出发"
        summary = (
            f"建议 {facts.recommended_departure_local} 出发，{base}{rain_clause}。"
        )
    return AdviceText(title=title[:200], summary=summary[:600])


def validate_model_text(candidate: AdviceText | None, fallback: AdviceText) -> AdviceText | None:
    """Reject invented numbers, weather alerts or malformed model output.

    Comparing numeric tokens against the deterministic template is deliberately
    strict: the model is allowed to rephrase, not to add a fact.
    """

    if candidate is None:
        return None
    title = candidate.title.strip()
    summary = candidate.summary.strip()
    if not title or not summary or len(title) > 200 or len(summary) > 600:
        return None
    if "\n\n" in summary or "\n\n" in title:
        return None
    if any(word in title + summary for word in ("预警", "警报", "红色预警")):
        return None
    fallback_text = fallback.title + fallback.summary
    candidate_text = title + summary
    allowed = set(NUMBER_RE.findall(fallback_text))
    produced = set(NUMBER_RE.findall(candidate_text))
    if not produced <= allowed:
        return None
    # A number is not interchangeable across units: the template's 20% rainfall
    # must not become a fabricated "20 minutes" ETA. Clock times are likewise
    # pinned to times the deterministic facts already contain.
    if not set(CLOCK_RE.findall(candidate_text)) <= set(CLOCK_RE.findall(fallback_text)):
        return None
    if not set(UNIT_RE.findall(candidate_text)) <= set(UNIT_RE.findall(fallback_text)):
        return None
    return AdviceText(title=title, summary=summary)


def build_advisory_values(
    *,
    plan: CommutePlan,
    instance,
    now: datetime,
    eta: EtaEvaluation,
    weather: WeatherEvaluation,
    previous: CommuteAdvisory | None,
) -> dict[str, Any]:
    """Compute every business field before optional wording is attempted."""

    now = ensure_aware(now)
    target = ensure_aware(instance.target_arrival_at)
    eta_seconds = eta.duration_seconds if eta.available else None
    extra_seconds = weather.extra_buffer_seconds
    recommended = (
        target
        - timedelta(seconds=eta_seconds or 0)
        - timedelta(minutes=plan.buffer_minutes)
        - timedelta(seconds=extra_seconds)
        if eta_seconds is not None
        else None
    )
    status = departure_status(recommended, now)
    facts = build_facts(
        plan=plan,
        instance=instance,
        now=now,
        eta=eta,
        weather=weather,
        recommended_departure_at=recommended,
        status=status,
    )
    template = render_template(facts)

    quality = "fresh"
    if not eta.available or not weather.available or weather.quality != "fresh":
        quality = "degraded"
    if not eta.available and not weather.available:
        quality = "unavailable"
    reason_codes = list(dict.fromkeys(eta.reason_codes + weather.reason_codes))
    if status == "past_due":
        reason_codes.append("DEPARTURE_PASSED")
    elif status == "leave_now":
        reason_codes.append("DEPARTURE_NOW")
    elif status == "upcoming":
        reason_codes.append("DEPARTURE_UPCOMING")
    else:
        reason_codes.append("DEPARTURE_UNKNOWN")
    if quality == "degraded":
        reason_codes.append("ADVISORY_DEGRADED")
    if quality == "unavailable":
        reason_codes.append("ADVISORY_UNAVAILABLE")

    sources = [weather_sources(weather, weather.provider)]
    sources.append(route_sources(eta))
    return {
        "generated_at": now,
        "expires_at": target,
        "quality": quality,
        "title": template.title,
        "summary": template.summary,
        "target_arrival_at": target,
        "recommended_departure_at": recommended,
        "departure_status": status,
        "eta_seconds": eta_seconds,
        "buffer_seconds": plan.buffer_minutes * 60,
        "weather_extra_buffer_seconds": extra_seconds,
        "weather": weather.summary,
        "sources": sources,
        "reason_codes": [{"code": code} for code in dict.fromkeys(reason_codes)],
        "explanation_source": "template",
        "facts": facts,
        "template": template,
    }


def material_change(
    previous: CommuteAdvisory | None, values: dict[str, Any]
) -> bool:
    """Only an earlier departure (>=10 min) or a rain-risk crossing is an update."""

    if previous is None:
        return True
    if previous.quality == "unavailable" and values["quality"] != "unavailable":
        return True
    current_departure = values.get("recommended_departure_at")
    previous_departure = previous.recommended_departure_at
    if current_departure is not None and previous_departure is not None:
        current = ensure_aware(current_departure)
        old = ensure_aware(previous_departure)
        if old - current >= timedelta(
            minutes=constants.DEPARTURE_EARLIER_THRESHOLD_MINUTES
        ):
            return True
    previous_weather = previous.weather_json or {}
    previous_rain = previous_weather.get("rain_risk")
    current_rain = values["weather"].get("rain_risk") if values["weather"] else None
    if previous_rain is not True and current_rain is True:
        return True
    if previous_rain is True and current_rain is False:
        return True
    return False


def notification_body(values: dict[str, Any]) -> tuple[str, str]:
    """Build lock-screen copy with no address label or coordinates."""

    title = values["title"]
    summary = values["summary"]
    if values["departure_status"] == "past_due":
        body = "建议出发时间已过，现在出发可能偏晚。打开查看详情。"
    elif values["quality"] == "unavailable":
        body = "天气和路况数据暂不可用，打开 App 可手动重试。"
    elif values["recommended_departure_at"] is not None:
        body = summary
    else:
        body = "路况数据暂不可用，无法给出精确出发时间。打开查看详情。"
    return title[:120], body[:300]
