"""Project attributable route and forecast facts into a short Bark message."""

from datetime import UTC, datetime
from unicodedata import east_asian_width
from zoneinfo import ZoneInfo

from assistant_api.commute.advice import evaluate_weather
from assistant_api.commute.providers.base import transit_line_label

MOBILITY = {"driving": "驾车", "walking": "步行", "bicycling": "骑行", "electrobike": "电动车", "transit": "公交"}
# Use the narrow end of the observed range, measured in half-width characters.
LOCK_SCREEN_LINE_WIDTH = 32
LOCK_SCREEN_BODY_LIMIT = 70
CHANGE_LABELS = {
    "severe_weather": ("天气有变", "新增雨雪或雷雨风险"),
    "new_rain_risk": ("下雨提醒", "新增降雨风险"),
    "boarding_changed": ("路线有变", "推荐线路已变"),
    "boarding_time_changed": ("到站有变", "预计到站时间有变"),
}


def weather_facts(result, *, plan, start, target, now, settings):
    """Use only overlapping forecast facts and short deterministic action phrases."""
    evaluation = evaluate_weather(result, window_start=start, window_end=target, now=now,
                                  plan=plan, freshness_seconds=settings.commute_weather_freshness_seconds)
    codes = {
        int(period.condition_code)
        for location in (result.origin, result.destination)
        for period in location.periods
        if period.condition_code and str(period.condition_code).isdigit()
        and period.start_at < target and period.end_at > start
    } if result is not None and evaluation.available else set()
    severe = any(code >= 95 or code in (66, 67, 75, 77, 82, 86) for code in codes)
    risk = evaluation.rain_risk
    temp = evaluation.temperature_c
    hint = "雷雨，注意安全" if any(code >= 95 for code in codes) else "雨雪，防滑" if severe else None
    if not hint and risk is True:
        hint = ("小雨" if codes & {51, 53, 55, 61, 80} else "有雨风险") + "，带伞"
    if not hint and temp is not None:
        hint = "添衣" if temp <= 12 else "注意防晒补水" if temp >= 32 else None
    if temp is not None:
        hint = f"{round(temp)}℃" + (f"｜{hint}" if hint else "")
    return {"hint": hint or "天气暂缺", "rain_risk": risk, "severe": severe,
            "temperature_c": temp, "extra_buffer_seconds": evaluation.extra_buffer_seconds,
            "fetched_at": evaluation.fetched_at.isoformat() if evaluation.fetched_at else None,
            "valid_until": evaluation.valid_to.isoformat() if evaluation.valid_to else None,
            "summary": evaluation.summary or None}


def instant(value):
    """Read one internally stored, timezone-bearing timestamp."""
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def verified_boarding(boarding, *, plan, day, now):
    """Project only server-sourced boarding times applicable to this service date."""
    if not boarding or not boarding.get("line_name"):
        return None
    result = {name: boarding.get(name) for name in ("provider", "line_id", "line_name",
                                                    "boarding_stop_id", "direction", "alighting_stop_id")}
    result["time_kind"] = "line_only"
    if not all(result.get(name) for name in ("provider", "line_id", "boarding_stop_id", "direction")):
        return result
    zone = ZoneInfo(plan.timezone)
    def for_day(key):
        """A provider time must identify an instant on the selected service date."""
        raw = boarding.get(key)
        try:
            dt = datetime.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            return None
        return dt if dt and dt.tzinfo and dt.astimezone(zone).date() == day else None

    kind = boarding.get("time_kind")
    scheduled = for_day("scheduled_boarding_at") if kind in {"scheduled", "live"} else None
    first = for_day("station_first_at") if kind in {"first_service", "scheduled", "live"} else None
    live = for_day("predicted_boarding_at")
    observed = for_day("observed_at")
    expires = for_day("valid_until")
    if first:
        result.update(time_kind="first_service", station_first_at=first.isoformat())
    if scheduled:
        result.update(time_kind="scheduled", scheduled_boarding_at=scheduled.isoformat())
    if (kind == "live" and boarding.get("trip_id") and
            live and observed and expires and observed <= now < expires and now < live):
        result.update(time_kind="live", predicted_boarding_at=live.isoformat(),
                      observed_at=observed.isoformat(), valid_until=expires.isoformat(), trip_id=boarding["trip_id"],
                      delay_seconds=boarding.get("delay_seconds"))
    return result


def lock_screen_width(text: str) -> int:
    """Approximate iPhone lock-screen text width in half-width characters."""
    return sum(2 if east_asian_width(char) in {"F", "W", "A"} else 1 for char in text)


def _minutes(seconds: int) -> int:
    """Keep positive durations visible even when they round below a minute."""
    return max(1, round(seconds / 60)) if seconds > 0 else 0


def _distance(meters: int | None) -> str:
    """Render a measured distance without adding a placeholder when it is missing."""
    if not meters:
        return ""
    return f"{meters}米" if meters < 1000 else f"{round(meters / 1000, 1):g}公里"


def _route_choices(snapshot, stage, reason, clock):
    """Offer route text from most to least specific without truncating station names."""
    details = snapshot.get("route_details") or {}
    boarding = snapshot.get("boarding") or {}
    line = transit_line_label(boarding.get("line_name") or "")
    lines = details.get("transit_lines") or ([line] if line else [])
    mode = snapshot.get("mode")
    if not lines:
        travel = MOBILITY.get(mode, "通勤") + _distance(snapshot.get("distance_meters"))
        road = details.get("main_road")
        return [f"{travel}｜经{' '.join(road.split())}", travel] if road else [travel]

    names = "转".join(" ".join(name.split()) for name in lines)
    prefix = "改乘" if reason == "boarding_changed" else "乘"
    travel = prefix + names
    origin = " ".join((details.get("boarding_stop_name") or "").split())
    destination = " ".join((details.get("alighting_stop_name") or "").split())
    stops = f"{origin}→{destination}" if origin and destination else (
        f"{origin}上车" if origin else f"{destination}下车" if destination else ""
    )
    choices = [f"{travel}（{stops}）"] if stops else []
    if len(lines) == 1 and line == lines[0]:
        if stage != "evening" and reason != "evening_fallback" and boarding.get("predicted_boarding_at"):
            choices.append(f"{line}预计{clock(boarding['predicted_boarding_at'])}到站")
        elif boarding.get("scheduled_boarding_at"):
            choices.append(f"参考{line}{clock(boarding['scheduled_boarding_at'])}班次")
        elif boarding.get("station_first_at"):
            choices.append(f"{line}首班{clock(boarding['station_first_at'])}")
    choices.append(travel)
    if len(lines) > 1:
        choices.append(f"{prefix}{' '.join(lines[0].split())}后换乘")
    return choices


def message(plan, stage, snapshot, reason, previous=None):
    """Fit the route, time or change, and weather into at most four estimated visible rows."""
    zone = ZoneInfo(plan.timezone)

    def clock(value):
        """Render an instant in the user's plan timezone."""
        return instant(value).astimezone(zone).strftime("%H:%M")

    departure = snapshot.get("departure_at")
    if not departure:
        return "暂未算出今早出发时间", "暂未查到路线\n出门前请查看当天通勤"

    hhmm = clock(departure)
    label = "今早" if reason == "first_usable_plan" else {"evening": "明早", "morning": "今早", "recheck": "改"}[stage]
    title = f"{label}{hhmm}出发"
    buffer = f"缓冲{_minutes(snapshot.get('buffer_seconds', 0))}分"
    duration = snapshot.get("duration_seconds")
    timing = f"全程{_minutes(duration)}分+{buffer}" if duration else buffer
    details = snapshot.get("route_details") or {}
    walking = details.get("walking_duration_seconds")
    walking_distance = _distance(snapshot.get("walking_distance_meters"))
    if walking or (walking_distance and snapshot.get("mode") != "walking"):
        walk = f"步行约{_minutes(walking)}分" if walking else f"含步行{walking_distance}"
        if lock_screen_width(f"{walk}｜{timing}") <= LOCK_SCREEN_LINE_WIDTH:
            timing = f"{walk}｜{timing}"

    if reason == "departure_earlier" and previous and previous.get("departure_at"):
        advanced = _minutes(int((instant(previous["departure_at"]) - instant(departure)).total_seconds()))
        title = f"改{hhmm}出发（提前{advanced}分）"
        if len(title) > 15:
            title = f"{hhmm}出发·提前{advanced}分"
        timing = f"原定{clock(previous['departure_at'])}｜{buffer}"
    elif reason in CHANGE_LABELS:
        change_title, change = CHANGE_LABELS[reason]
        title = f"{change_title}·{hhmm}出发"
        timing = f"{change}｜{buffer}"
    elif reason == "evening_fallback":
        title = f"暂按{hhmm}出发"
        timing = "今早未刷新｜昨晚方案"

    weather = snapshot.get("weather") or {}
    hint = " ".join((weather.get("hint") or "天气暂缺").split())
    if lock_screen_width(hint) > LOCK_SCREEN_LINE_WIDTH:
        hint = "注意雨雪" if weather.get("severe") else "带伞" if weather.get("rain_risk") else "出门前查看天气"
    route_limit = LOCK_SCREEN_BODY_LIMIT - len(timing) - len(hint) - 2
    # Reserve space for wrapping at words/punctuation, keeping the other rows intact.
    route = next((choice for choice in _route_choices(snapshot, stage, reason, clock)
                  if len(choice) <= route_limit and lock_screen_width(choice) <= 2 * LOCK_SCREEN_LINE_WIDTH - 4),
                 f"{MOBILITY.get(snapshot.get('mode'), '通勤')}｜查看路线")
    return title, f"{route}\n{timing}\n{hint}"
