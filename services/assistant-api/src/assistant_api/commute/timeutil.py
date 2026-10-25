"""Deterministic local-time rules, including DST gaps and fold selection.

The scheduler must never derive "today" or an arrival instant from the server's
local timezone. These functions are pure so DST behavior is testable with fixed
clocks and needs no database.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
WEEKDAY_MIN, WEEKDAY_MAX = 1, 7


class InvalidCommuteTime(ValueError):
    """Report an invalid timezone, clock time or weekday list."""


def parse_hhmm(value: str) -> tuple[int, int]:
    """Parse strict 24-hour HH:mm; 24:00 and seconds are rejected."""

    match = HHMM_RE.fullmatch(value or "")
    if match is None:
        raise InvalidCommuteTime("时间必须为 HH:mm（00:00–23:59）。")
    return int(match.group(1)), int(match.group(2))


def hhmm_minutes(value: str) -> int:
    hour, minute = parse_hhmm(value)
    return hour * 60 + minute


def validate_timezone(value: str) -> str:
    """Accept IANA names only; ZoneInfo rejects offsets and unknown regions."""

    if value == "UTC":
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise InvalidCommuteTime("时区必须是有效的 IANA 时区。") from exc
    if value.startswith(("+", "-")):
        raise InvalidCommuteTime("时区必须是有效的 IANA 时区。")
    return value


def validate_weekdays(values: list[int]) -> list[int]:
    """Normalize ISO weekdays 1..7, sorted and unique; at least one required."""

    if not values:
        raise InvalidCommuteTime("至少选择一个星期几。")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise InvalidCommuteTime("星期必须使用 1–7 的整数。")
    if any(v < WEEKDAY_MIN or v > WEEKDAY_MAX for v in values):
        raise InvalidCommuteTime("星期必须使用 1–7 的整数。")
    return sorted(set(values))


def local_today(now: datetime, timezone: str) -> date:
    """Return the plan-local service date for an aware instant."""

    return ensure_aware(now).astimezone(ZoneInfo(timezone)).date()


def ensure_aware(value: datetime) -> datetime:
    """Treat naive database values as UTC; never silently use server local time."""

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def resolve_local_minutes(day: date, minutes: int, timezone: str) -> datetime:
    """Resolve a wall-clock minute to the first valid UTC instant.

    For a DST gap (e.g. 02:30 in New York on the spring-forward day) the local
    time does not exist. We advance one minute at a time until the wall clock
    round-trips, which lands on the first valid instant (03:00). For a repeated
    time (fall-back) `fold=0` selects the first occurrence, and the scheduler's
    per-slot unique key ensures the second occurrence is not executed twice.
    """

    tz = ZoneInfo(validate_timezone(timezone))
    total = max(0, min(minutes, 24 * 60 - 1))
    naive = datetime.combine(day, time(hour=total // 60, minute=total % 60))
    for shift in range(0, 26 * 60):
        candidate = naive + timedelta(minutes=shift)
        for fold in (0, 1):
            aware = candidate.replace(tzinfo=tz, fold=fold)
            roundtrip = aware.astimezone(UTC).astimezone(tz)
            if (
                roundtrip.replace(tzinfo=None) == candidate
                and roundtrip.fold == fold
            ):
                return aware.astimezone(UTC)
    raise InvalidCommuteTime("无法解析该本地时间。")  # pragma: no cover - defensive


def resolve_local_hhmm(day: date, value: str, timezone: str) -> datetime:
    """Resolve HH:mm on one local date, applying the DST rules above."""

    return resolve_local_minutes(day, hhmm_minutes(value), timezone)


def check_window_for(day: date, check_local_time: str, timezone: str) -> datetime:
    return resolve_local_hhmm(day, check_local_time, timezone)


def target_arrival_for(day: date, arrival_local_time: str, timezone: str) -> datetime:
    return resolve_local_hhmm(day, arrival_local_time, timezone)


def slot_index_for(now: datetime, timezone: str, interval_minutes: int = 10) -> int:
    """Return the plan-local check interval slot containing an instant.

    Using local minute-of-day means the repeated hour of a fall-back night maps
    to the same slot as its first occurrence, so the unique check-slot constraint
    enforces "execute the repeated local time only once".
    """

    local = ensure_aware(now).astimezone(ZoneInfo(timezone))
    interval = max(1, interval_minutes)
    return (local.hour * 60 + local.minute) // interval


def iso_weekday(day: date) -> int:
    return day.isoweekday()


def utc_iso(value: datetime | None) -> str | None:
    """Serialize to UTC ISO 8601 with `Z`; None remains None."""

    if value is None:
        return None
    return ensure_aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_finite_number(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def format_minutes(seconds: int | float | None) -> str | None:
    """Round seconds up to whole minutes for human display."""

    if seconds is None:
        return None
    return str(max(0, math.ceil(seconds / 60)))


def validate_same_day(check_local_time: str, arrival_local_time: str) -> None:
    """First version rejects cross-midnight plans with an explicit message."""

    if hhmm_minutes(check_local_time) >= hhmm_minutes(arrival_local_time):
        raise InvalidCommuteTime("第一版仅支持同一天内完成通勤，检查时间必须早于目标到达时间；跨午夜暂不支持。")
