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

    result, _reason = await validation_for_schedule(db, user, schedule)
    return result


async def validation_for_schedule(
    db: AsyncSession,
    user: User,
    schedule: ScheduleEvent,
) -> tuple[dict | None, str | None]:
    """Return the current result or the exact reason it cannot be shown."""

    row = await db.scalar(
        select(ScheduleRouteResult).where(
            ScheduleRouteResult.schedule_id == schedule.schedule_id,
            ScheduleRouteResult.user_id == user.user_id,
        )
    )
    if row is None:
        return None, "no_linked_route_result"
    if row.schedule_fingerprint != schedule_fingerprint(schedule):
        return None, "route_result_stale"
    result = dict(row.result_json or {})
    max_age = (
        PLANNED_RESULT_MAX_AGE
        if result.get("timing_basis") == "planned_time_not_live_forecast"
        else LIVE_RESULT_MAX_AGE
    )
    if datetime.now(UTC) - ensure_aware(row.calculated_at) > timedelta(seconds=max_age):
        return None, "route_result_expired"
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
            return None, "origin_schedule_changed"
    result["calculated_at"] = row.calculated_at
    return result, None


def unverified_message(reason_code: str) -> str:
    """Return the public explanation for one unverified route reason."""

    return {
        "no_linked_route_result": "尚无与该日程关联的路线结果。",
        "route_result_stale": "日程或地点已变化，原路线结果不再有效。",
        "route_result_expired": "原路线结果已过期，请重新测算。",
        "origin_schedule_changed": "起点安排已变化，原路线结果不再有效。",
    }.get(reason_code, "路线结果当前不可用。")


def _unverified(schedule_id: str, reason_code: str) -> dict:
    """Build the stable read response for a missing or invalid saved route."""

    return {
        "status": "unverified",
        "reason_code": reason_code,
        "message": unverified_message(reason_code),
        "schedule_id": schedule_id,
        "provider": None,
        "mode": None,
        "duration_seconds": None,
        "distance_meters": None,
        "walking_distance_meters": None,
        "transfer_count": None,
        "transfers_summary": None,
        "fare_cny": None,
        "fare_kind": None,
        "fare_notice": None,
        "origin_basis": None,
        "origin_schedule_id": None,
        "fetched_at": None,
        "requested_depart_at": None,
        "target_arrival_at": None,
        "calculated_at": None,
        "timing_basis": None,
        "accuracy_notice": None,
        "origin": None,
        "destination": None,
        "shape": {"status": "missing", "segments": []},
    }


async def detail_for_schedule(
    db: AsyncSession,
    user: User,
    schedule_id: str,
) -> dict:
    """Read one saved route shape without measuring or writing anything."""

    schedule = await db.scalar(
        select(ScheduleEvent).where(
            ScheduleEvent.schedule_id == schedule_id,
            ScheduleEvent.user_id == user.user_id,
            ScheduleEvent.deleted_at.is_(None),
        )
    )
    if schedule is None:
        raise NotFoundError("schedule_not_found", "日程不存在。")
    result, reason = await validation_for_schedule(db, user, schedule)
    if result is None:
        return _unverified(schedule_id, reason or "no_linked_route_result")
    shape = result.get("shape")
    if not isinstance(shape, dict) or shape.get("status") not in {
        "available",
        "partial",
        "missing",
    }:
        shape = {"status": "missing", "segments": []}
    return {
        "status": "available",
        "reason_code": None,
        "message": None,
        "schedule_id": schedule_id,
        "provider": result.get("provider"),
        "mode": result.get("mode"),
        "duration_seconds": result.get("duration_seconds"),
        "distance_meters": result.get("distance_meters"),
        "walking_distance_meters": result.get("walking_distance_meters"),
        "transfer_count": result.get("transfer_count"),
        "transfers_summary": result.get("transfers_summary"),
        "fare_cny": result.get("fare_cny"),
        "fare_kind": result.get("fare_kind"),
        "fare_notice": result.get("fare_notice"),
        "origin_basis": result.get("origin_basis"),
        "origin_schedule_id": result.get("origin_schedule_id"),
        "fetched_at": result.get("fetched_at"),
        "requested_depart_at": result.get("request_depart_at"),
        "target_arrival_at": result.get("target_arrival_at"),
        "calculated_at": result.get("calculated_at"),
        "timing_basis": result.get("timing_basis"),
        "accuracy_notice": result.get("accuracy_notice"),
        "origin": result.get("origin"),
        "destination": result.get("destination"),
        "shape": shape,
    }


__all__ = [
    "detail_for_schedule",
    "save_for_schedule",
    "schedule_fingerprint",
    "unverified_message",
    "valid_for_schedule",
    "validation_for_schedule",
]
