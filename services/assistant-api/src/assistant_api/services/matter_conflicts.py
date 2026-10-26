"""Schedule conflicts around a proposed appointment.

Range: everything that starts or ends within N hours of the proposal, including
imported Apple calendar rows, but only when their data source is still authorized
for assistant use. The 15-minute slack rule and 2-3 options follow the product
plan; a missing route provider produces an explicit "route unavailable" note
instead of the script's illustrative 25 minutes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import ContentSource, ScheduleEvent, User
from assistant_api.services.commute_estimate import estimate

EXCLUDED_STATUSES = ("cancelled", "deleted")


def _round_up_minutes(value: datetime) -> datetime:
    discard = value.second or value.microsecond
    if not discard:
        return value
    return value + timedelta(minutes=1, seconds=-value.second, microseconds=-value.microsecond)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_aware(value).astimezone(__import__("datetime").UTC).isoformat().replace(
        "+00:00", "Z"
    )


async def _authorized_source_ids(db: AsyncSession, user_id: str) -> set[str]:
    rows = (
        await db.scalars(
            select(ContentSource.source_id).where(
                ContentSource.user_id == user_id,
                ContentSource.assistant_enabled.is_(True),
            )
        )
    ).all()
    return set(rows)


async def evaluate_conflict(
    db: AsyncSession,
    runtime: CommuteRuntime,
    *,
    user: User,
    starts_at: datetime,
    ends_at: datetime | None,
    latitude: float | None = None,
    longitude: float | None = None,
    exclude_schedule_id: str | None = None,
    window_hours: int = 3,
    min_gap_seconds: int = 900,
) -> dict:
    """Return has_conflict, the conflicting neighbours and 2-3 options."""

    start = ensure_aware(starts_at)
    end = ensure_aware(ends_at) if ends_at is not None else start + timedelta(hours=1)
    if end < start:
        end = start
    window_start = start - timedelta(hours=window_hours)
    window_end = end + timedelta(hours=window_hours)

    statement = select(ScheduleEvent).where(
        ScheduleEvent.user_id == user.user_id,
        ScheduleEvent.deleted_at.is_(None),
        ScheduleEvent.status.notin_(EXCLUDED_STATUSES),
        ScheduleEvent.starts_at < window_end,
        or_(
            ScheduleEvent.ends_at >= window_start,
            (ScheduleEvent.ends_at.is_(None))
            & (ScheduleEvent.starts_at >= window_start),
        ),
    )
    if exclude_schedule_id:
        statement = statement.where(ScheduleEvent.schedule_id != exclude_schedule_id)
    candidates = list((await db.scalars(statement)).all())

    authorized = await _authorized_source_ids(db, user.user_id)
    neighbours = [
        event
        for event in candidates
        if event.source in (None, "web_native", "ios_reminders")
        or event.data_source_id in authorized
    ]
    neighbours.sort(key=lambda event: ensure_aware(event.starts_at))

    previous = None
    following = None
    for event in neighbours:
        event_end = ensure_aware(event.ends_at) if event.ends_at else ensure_aware(event.starts_at)
        if event_end <= start:
            if previous is None or event_end > (
                ensure_aware(previous.ends_at) if previous.ends_at else ensure_aware(previous.starts_at)
            ):
                previous = event
        elif ensure_aware(event.starts_at) >= end:
            if following is None or ensure_aware(event.starts_at) < ensure_aware(following.starts_at):
                following = event

    conflicts: list[dict] = []
    options: list[dict] = []
    severity = "none"
    eta_note = None

    if previous is not None:
        previous_end = (
            ensure_aware(previous.ends_at)
            if previous.ends_at
            else ensure_aware(previous.starts_at)
        )
        gap = int((start - previous_end).total_seconds())
        overlap = gap < 0
        eta = None
        eta_status = "not_needed"
        if (
            latitude is not None
            and longitude is not None
            and previous.latitude is not None
            and previous.longitude is not None
        ):
            measurement = await estimate(
                runtime,
                origin=(previous.latitude, previous.longitude),
                destination=(latitude, longitude),
                mode="driving",
                depart_at=previous_end,
                arrive_by=start,
            )
            if measurement["status"] == "available":
                eta = measurement["duration_seconds"]
                eta_status = "available"
            else:
                eta_status = "unavailable"
                eta_note = measurement["reason_code"]
        required = (eta if eta is not None else 0) + min_gap_seconds
        risky = overlap or gap < required
        if risky:
            severity = "overlap" if overlap else "tight"
            conflicts.append(
                {
                    "schedule_id": previous.schedule_id,
                    "title": previous.title,
                    "starts_at": ensure_aware(previous.starts_at),
                    "ends_at": previous.ends_at,
                    "gap_seconds": gap,
                    "eta_seconds": eta,
                    "eta_status": eta_status,
                    "reason": "overlap" if overlap else (
                        "eta_short" if eta is not None else "gap_short_route_unknown"
                    ),
                }
            )
            shortfall = max(0, required - gap)
            shifted = _round_up_minutes(start + timedelta(seconds=shortfall))
            options.append(
                {
                    "option_id": "A",
                    "label": "推迟本次安排",
                    "detail": f"把开始时间调整到 {shifted.astimezone(__import__('datetime').timezone.utc).strftime('%H:%M')}（UTC）左右，留出路程与 15 分钟余量。",
                    "recommended": True,
                    "proposed_start": shifted,
                    "creates_todo": None,
                }
            )
            options.append(
                {
                    "option_id": "B",
                    "label": "调整上一条安排",
                    "detail": f"把「{previous.title}」提前约 {max(5, (shortfall // 60) + 1)} 分钟，"
                    "并为联系人增加一条改期待办。",
                    "recommended": False,
                    "proposed_start": None,
                    "creates_todo": f"联系相关方调整「{previous.title}」时间",
                }
            )
            options.append(
                {
                    "option_id": "C",
                    "label": "保持时间，提前出发",
                    "detail": "按原时间进行，但提前结束上一项或缩短停留；路线不可用时需自行确认路况。",
                    "recommended": False,
                    "proposed_start": None,
                    "creates_todo": None,
                }
            )

    if following is not None and severity != "overlap":
        following_start = ensure_aware(following.starts_at)
        gap_after = int((following_start - end).total_seconds())
        if gap_after < min_gap_seconds:
            severity = "tight"
            conflicts.append(
                {
                    "schedule_id": following.schedule_id,
                    "title": following.title,
                    "starts_at": following_start,
                    "ends_at": following.ends_at,
                    "gap_seconds": gap_after,
                    "eta_seconds": None,
                    "eta_status": "not_needed",
                    "reason": "next_too_close",
                }
            )
            if not options:
                options.append(
                    {
                        "option_id": "A",
                        "label": "提前结束本次安排",
                        "detail": f"下一项「{following.title}」开始得很近，建议提前 "
                        f"{max(5, (min_gap_seconds - gap_after) // 60 + 1)} 分钟结束。",
                        "recommended": True,
                        "proposed_start": None,
                        "creates_todo": None,
                    }
                )
                options.append(
                    {
                        "option_id": "B",
                        "label": "顺延下一项",
                        "detail": f"与相关方确认把「{following.title}」推迟至少 "
                        f"{max(5, (min_gap_seconds - gap_after) // 60 + 1)} 分钟。",
                        "recommended": False,
                        "proposed_start": None,
                        "creates_todo": f"联系相关方顺延「{following.title}」",
                    }
                )

    return {
        "has_conflict": bool(conflicts),
        "severity": severity,
        "window": {"start": _iso(window_start), "end": _iso(window_end)},
        "conflicts": conflicts,
        "options": options,
        "eta_note": eta_note,
    }


__all__ = ["evaluate_conflict"]
