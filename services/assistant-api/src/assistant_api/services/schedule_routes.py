"""Link route measurements to a formal schedule and invalidate them on edits."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.errors import NotFoundError
from assistant_api.models import ScheduleEvent, ScheduleRouteResult, User

PLANNED_RESULT_MAX_AGE = 24 * 60 * 60
LIVE_RESULT_MAX_AGE = 15 * 60


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def schedule_fingerprint(schedule: ScheduleEvent) -> str:
    """Hash exactly the fields that make a linked route result stale."""

    value = {
        "starts_at": _iso(schedule.starts_at),
        "ends_at": _iso(schedule.ends_at),
        "location": schedule.location,
        "latitude": schedule.latitude,
        "longitude": schedule.longitude,
        "coordinate_system": schedule.coordinate_system,
        "location_city": schedule.location_city,
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable(value):
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


async def save_for_schedule(
    db: AsyncSession,
    user: User,
    *,
    schedule_id: str,
    result: dict,
) -> dict:
    """Persist one successful result for the schedule's current fingerprint."""

    schedule = await db.scalar(
        select(ScheduleEvent).where(
            ScheduleEvent.schedule_id == schedule_id,
            ScheduleEvent.user_id == user.user_id,
            ScheduleEvent.deleted_at.is_(None),
        )
    )
    if schedule is None:
        raise NotFoundError("schedule_not_found", "日程不存在。")
    fingerprint = schedule_fingerprint(schedule)
    row = await db.scalar(
        select(ScheduleRouteResult).where(
            ScheduleRouteResult.schedule_id == schedule.schedule_id,
            ScheduleRouteResult.user_id == user.user_id,
        )
    )
    if row is None:
        row = ScheduleRouteResult(
            user_id=user.user_id,
            schedule_id=schedule.schedule_id,
            schedule_fingerprint=fingerprint,
            result_json={},
            calculated_at=datetime.now(UTC),
        )
        db.add(row)
    row.schedule_fingerprint = fingerprint
    row.result_json = _jsonable(result)
    row.calculated_at = datetime.now(UTC)
    await db.commit()
    return row.result_json


async def valid_for_schedule(
    db: AsyncSession,
    user: User,
    schedule: ScheduleEvent,
) -> dict | None:
    """Return a linked result only while the schedule fingerprint is unchanged."""

    row = await db.scalar(
        select(ScheduleRouteResult).where(
            ScheduleRouteResult.schedule_id == schedule.schedule_id,
            ScheduleRouteResult.user_id == user.user_id,
        )
    )
    if row is None or row.schedule_fingerprint != schedule_fingerprint(schedule):
        return None
    result = dict(row.result_json or {})
    max_age = (
        PLANNED_RESULT_MAX_AGE
        if result.get("timing_basis") == "planned_time_not_live_forecast"
        else LIVE_RESULT_MAX_AGE
    )
    if datetime.now(UTC) - ensure_aware(row.calculated_at) > timedelta(seconds=max_age):
        return None
    origin_schedule_id = result.get("origin_schedule_id")
    if origin_schedule_id:
        origin = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == origin_schedule_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
            )
        )
        if (
            origin is None
            or result.get("origin_schedule_fingerprint")
            != schedule_fingerprint(origin)
        ):
            return None
    result["calculated_at"] = row.calculated_at
    return result


__all__ = ["save_for_schedule", "schedule_fingerprint", "valid_for_schedule"]
