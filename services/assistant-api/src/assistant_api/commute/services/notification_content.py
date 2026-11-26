"""Project attributable route and forecast facts into a short Bark message."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from assistant_api.commute.advice import evaluate_weather

MOBILITY = {"driving": "驾车", "walking": "步行", "bicycling": "骑行", "electrobike": "电动车", "transit": "公交"}


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


def message(plan, stage, snapshot, reason, previous=None):
    """Keep times and actions in the first two lines without guessing a transit time."""
    zone = ZoneInfo(plan.timezone)
    def clock(value):
        """Render an instant in the user's plan timezone."""
        return instant(value).astimezone(zone).strftime("%H:%M")
    target = clock(snapshot["target_arrival_at"])
    departure = snapshot.get("departure_at")
    label = "今早" if reason == "first_usable_plan" else {"evening": "明早", "morning": "今早", "recheck": "改"}[stage]
    if not departure:
        return "暂未算出今早出发时间", f"目标{target}到｜路线暂缺\n出门前请查看当天通勤"
    hhmm = clock(departure)
    title = f"{label}{hhmm}出发"
    weather = snapshot.get("weather") or {}
    weather_hint = weather.get("hint") or "天气暂缺"
    boarding = snapshot.get("boarding") or {}
    line = (boarding.get("line_name") or "").split("(")[0].split("（")[0].strip()
    line = " ".join(line.split())
    if len(line) > 8:
        line = line[:7] + "…"
    travel = f"{'改乘' if reason == 'boarding_changed' else '乘'}{line}" if line else MOBILITY.get(snapshot.get("mode"), "通勤")
    if stage != "evening" and boarding.get("predicted_boarding_at"):
        travel = f"{line}预计{clock(boarding['predicted_boarding_at'])}到站"
    elif boarding.get("scheduled_boarding_at"):
        travel = f"参考{line}{clock(boarding['scheduled_boarding_at'])}班次"
    elif boarding.get("station_first_at"):
        travel = f"{line}首班{clock(boarding['station_first_at'])}"
    if reason == "evening_fallback" and boarding.get("predicted_boarding_at") and not boarding.get("scheduled_boarding_at"):
        travel = f"乘{line}"
    if snapshot.get("duration_seconds") and not line:
        travel += f"约{round(snapshot['duration_seconds'] / 60)}分"
    if reason == "evening_fallback":
        title = f"暂按{hhmm}出发"
        second = f"今早未更新｜{weather_hint}"
    else:
        second = weather_hint
    first = f"{travel}｜{target}到"
    if len(first) + len(second) > 40:
        second = ("今早未更新｜" if reason == "evening_fallback" else "") + (
            "注意雨雪" if weather.get("severe") else "带伞" if weather.get("rain_risk") else "查看天气")
    extra = (f"按{target}到达倒推出发时间。全程约{round(snapshot['duration_seconds'] / 60)}分钟，"
             f"另留{round(snapshot['buffer_seconds'] / 60)}分钟缓冲。")
    if snapshot.get("saved_at") and reason != "evening_fallback":
        extra += f"路线于{clock(snapshot['saved_at'])}测算。"
    if previous and reason == "departure_earlier":
        extra = f"原定{clock(previous['departure_at'])}出发。{extra}"
    elif reason == "evening_fallback":
        extra = f"沿用昨晚{clock(snapshot['saved_at'])}测算。{extra}"
    elif reason in ("severe_weather", "new_rain_risk", "boarding_changed", "boarding_time_changed"):
        changes = {"severe_weather": "新增恶劣天气", "new_rain_risk": "新增降雨风险",
                   "boarding_changed": "主力线路已调整", "boarding_time_changed": "到站预计时间有变"}
        extra = changes[reason] + "。" + extra
    return title, f"{first}\n{second}\n{extra}"
