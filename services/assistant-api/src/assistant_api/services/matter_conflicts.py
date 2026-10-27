"""Schedule conflicts around a proposed appointment.

Window: N hours around the proposal (default 3). Sources: app-native schedules,
imported iOS schedules whose data source is still authorized for assistant use,
and calendar copies uploaded through the older content-items entry point. Long
events that start before the window but end inside it are included.

The check covers both travel legs (previous -> proposal, proposal -> next) and
direct overlap/containment. When coordinates are missing or the route provider
fails, the leg is reported as "unverified"; it is never silently treated as fine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import (
    ContentItem,
    ContentSource,
    Matter,
    ScheduleEvent,
    User,
)
from assistant_api.services.commute_estimate import estimate

EXCLUDED_STATUSES = ("cancelled", "deleted")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _round_up_minutes(value: datetime) -> datetime:
    if not (value.second or value.microsecond):
        return value
    return value + timedelta(minutes=1, seconds=-value.second, microseconds=-value.microsecond)


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


def can_edit_schedule(source: str | None, data_source_id: str | None) -> bool:
    """Only app-native schedules may be moved by an adjustment option.

    A NULL source is retained for legacy app rows. Any imported record (including
    an EventKit copy without a current data-source id) is read-only here.
    """

    return source in (None, "web_native") and data_source_id is None


def _block(
    *,
    key: str,
    schedule_id: str | None,
    title: str,
    start: datetime,
    end: datetime | None,
    location: str | None,
    latitude: float | None,
    longitude: float | None,
    source: str,
    matter_id: str | None = None,
    data_source_id: str | None = None,
    matter_title: str | None = None,
    editable: bool = False,
) -> dict:
    start = ensure_aware(start)
    finish = ensure_aware(end) if end is not None else start
    return {
        "key": key,
        "schedule_id": schedule_id,
        "title": title,
        "start": start,
        "end": finish,
        "location": location,
        "latitude": latitude,
        "longitude": longitude,
        "source": source,
        "matter_id": matter_id,
        "data_source_id": data_source_id,
        "matter_title": matter_title,
        "editable": editable,
    }


async def _collect_blocks(
    db: AsyncSession,
    user: User,
    *,
    window_start: datetime,
    window_end: datetime,
    exclude_schedule_id: str | None,
) -> list[dict]:
    authorized = await _authorized_source_ids(db, user.user_id)
    schedule_rows = (
        await db.scalars(
            select(ScheduleEvent).where(
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
                ScheduleEvent.status.notin_(EXCLUDED_STATUSES),
                ScheduleEvent.starts_at < window_end,
                or_(
                    ScheduleEvent.ends_at >= window_start,
                    and_(
                        ScheduleEvent.ends_at.is_(None),
                        ScheduleEvent.starts_at >= window_start,
                    ),
                ),
            )
        )
    ).all()
    blocks: list[dict] = []
    for event in schedule_rows:
        if exclude_schedule_id and event.schedule_id == exclude_schedule_id:
            continue
        if event.data_source_id is not None and event.data_source_id not in authorized:
            continue
        blocks.append(
            _block(
                key=f"schedule:{event.schedule_id}",
                schedule_id=event.schedule_id,
                title=event.title,
                start=event.starts_at,
                end=event.ends_at,
                location=event.location,
                latitude=event.latitude,
                longitude=event.longitude,
                source=event.source or "web_native",
                matter_id=event.matter_id,
                data_source_id=event.data_source_id,
                editable=can_edit_schedule(event.source, event.data_source_id),
            )
        )

    content_rows = (
        await db.execute(
            select(ContentItem, ContentSource)
            .join(ContentSource, ContentSource.source_id == ContentItem.source_id)
            .where(
                ContentItem.user_id == user.user_id,
                ContentItem.kind == "calendar_event",
                ContentItem.deleted_at.is_(None),
                ContentSource.assistant_enabled.is_(True),
                ContentItem.starts_at.is_not(None),
                ContentItem.starts_at < window_end,
                or_(
                    ContentItem.ends_at >= window_start,
                    ContentItem.ends_at.is_(None),
                ),
            )
        )
    ).all()
    for item, source in content_rows:
        allowed = set(source.upload_fields_json or [])
        blocks.append(
            _block(
                key=f"content:{item.item_id}",
                schedule_id=None,
                title=item.title if "title" in allowed else "",
                start=item.starts_at,
                end=item.ends_at,
                location=item.location if "location" in allowed else None,
                latitude=None,
                longitude=None,
                source="ios_eventkit",
            )
        )
    return blocks


def _dedupe(blocks: list[dict]) -> list[dict]:
    """The same event can exist in both the old and new storage paths."""

    seen: dict[tuple, dict] = {}
    for block in blocks:
        key = (
            block["title"],
            block["start"],
            block["end"],
            block["location"],
        )
        if key in seen:
            if seen[key]["latitude"] is None and block["latitude"] is not None:
                seen[key] = block
            continue
        seen[key] = block
    return list(seen.values())


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
    adjusted: dict[str, tuple[datetime, datetime | None]] | None = None,
) -> dict:
    """Return has_conflict, overlapping/neighbouring blocks and 2-3 options."""

    start = ensure_aware(starts_at)
    end = ensure_aware(ends_at) if ends_at is not None else start + timedelta(hours=1)
    if end < start:
        end = start
    window_start = start - timedelta(hours=window_hours)
    window_end = end + timedelta(hours=window_hours)
    blocks = await _collect_blocks(
        db,
        user,
        window_start=window_start,
        window_end=window_end,
        exclude_schedule_id=exclude_schedule_id,
    )
    if adjusted:
        for block in blocks:
            schedule_id = block["schedule_id"]
            if schedule_id and schedule_id in adjusted:
                new_start, new_end = adjusted[schedule_id]
                block["start"] = ensure_aware(new_start)
                block["end"] = (
                    ensure_aware(new_end) if new_end is not None else block["start"]
                )
    blocks = _dedupe(blocks)
    matter_ids = {block["matter_id"] for block in blocks if block["matter_id"]}
    matter_titles: dict[str, str] = {}
    if matter_ids:
        matter_rows = (
            await db.scalars(
                select(Matter).where(
                    Matter.user_id == user.user_id,
                    Matter.matter_id.in_(matter_ids),
                )
            )
        ).all()
        matter_titles = {row.matter_id: row.title for row in matter_rows}
    for block in blocks:
        if block["matter_id"]:
            block["matter_title"] = matter_titles.get(block["matter_id"])

    overlaps = [b for b in blocks if b["start"] < end and b["end"] > start]
    previous = None
    following = None
    for block in blocks:
        if block["start"] >= start and block["end"] <= end:
            continue
        if block["end"] <= start:
            if previous is None or block["end"] > previous["end"]:
                previous = block
        elif block["start"] >= end:
            if following is None or block["start"] < following["start"]:
                following = block

    conflicts: list[dict] = []
    options: list[dict] = []
    eta_notes: list[str] = []

    for block in overlaps:
        conflicts.append(
            {
                "schedule_id": block["schedule_id"],
                "title": block["title"],
                "starts_at": block["start"],
                "ends_at": block["end"],
                "gap_seconds": int((block["start"] - start).total_seconds()),
                "eta_seconds": None,
                "eta_status": "not_applicable",
                "relation": "overlap",
                "reason": "overlap",
            }
        )

    async def travel_leg(block: dict, *, direction: str) -> dict | None:
        if direction == "previous":
            gap = int((start - block["end"]).total_seconds())
        else:
            gap = int((block["start"] - end).total_seconds())
        leg_lat = latitude if direction == "previous" else block["latitude"]
        leg_lng = longitude if direction == "previous" else block["longitude"]
        origin_lat = block["latitude"] if direction == "previous" else latitude
        origin_lng = block["longitude"] if direction == "previous" else longitude
        eta = None
        eta_status = "unverified"
        if None not in (leg_lat, leg_lng, origin_lat, origin_lng):
            measurement = await estimate(
                runtime,
                origin=(origin_lat, origin_lng),
                destination=(leg_lat, leg_lng),
                mode="driving",
                depart_at=block["end"] if direction == "previous" else end,
                arrive_by=start if direction == "previous" else block["start"],
            )
            if measurement["status"] == "available":
                eta = measurement["duration_seconds"]
                eta_status = "available"
            else:
                eta_notes.append(measurement["reason_code"])
        else:
            eta_notes.append("coordinates_missing")
        required = (eta if eta is not None else 0) + min_gap_seconds
        risky = eta is None and gap < min_gap_seconds
        risky = risky or (eta is not None and gap < required)
        if not risky:
            return None
        return {
            "schedule_id": block["schedule_id"],
            "title": block["title"],
            "starts_at": block["start"],
            "ends_at": block["end"],
            "gap_seconds": gap,
            "eta_seconds": eta,
            "eta_status": eta_status,
            "relation": direction,
            "reason": (
                "gap_short_route_unverified"
                if eta is None
                else ("eta_short" if direction == "previous" else "next_too_close")
            ),
        }

    if previous is not None:
        item = await travel_leg(previous, direction="previous")
        if item is not None:
            conflicts.append(item)
    if following is not None:
        item = await travel_leg(following, direction="next")
        if item is not None:
            conflicts.append(item)

    severity = "none"
    if any(item["relation"] == "overlap" for item in conflicts):
        severity = "overlap"
    elif conflicts:
        severity = "tight"

    if conflicts:
        worst = conflicts[0]
        # Option A: move this appointment; option B: move the neighbour;
        # option C: keep the time and accept the risk (requires acknowledgement).
        if worst["relation"] == "previous":
            required = (worst["eta_seconds"] or 0) + min_gap_seconds
            shift = max(0, required - worst["gap_seconds"])
            target = previous
            direction = 1
        elif worst["relation"] == "next":
            required = (worst["eta_seconds"] or 0) + min_gap_seconds
            shift = max(0, required - worst["gap_seconds"])
            target = following
            direction = -1
        else:
            # Move past every overlapping block and keep the same 15-minute
            # buffer used for neighbouring appointments. Moving only to the
            # block start (or its exact end) can still overlap or leave no gap.
            overlap_end = max(block["end"] for block in overlaps)
            target_start = overlap_end + timedelta(seconds=min_gap_seconds)
            shift = max(0, int((target_start - start).total_seconds()))
            target = None
            direction = 1
        shift = int((shift + 299) // 300 * 300)
        shift = direction * shift
        proposed = _round_up_minutes(start + timedelta(seconds=shift))
        route_unverified = any(
            item["eta_status"] == "unverified" for item in conflicts
        )
        route_note = "路程仍未核实，应用后请重新核对。" if route_unverified else ""
        if worst["relation"] == "overlap":
            option_a_detail = (
                f"把本次开始时间移动到 {proposed.astimezone(UTC).strftime('%H:%M')}（UTC），"
                "避开当前重叠；应用后会重新计算剩余冲突。"
            )
        elif direction > 0:
            option_a_detail = (
                f"把本次安排整体推迟约 {max(1, abs(shift) // 60)} 分钟，"
                f"开始时间约 {proposed.astimezone(UTC).strftime('%H:%M')}（UTC）；"
                "应用后会重新计算剩余冲突。"
            )
        else:
            option_a_detail = (
                f"把本次安排整体提前约 {max(1, abs(shift) // 60)} 分钟，"
                f"开始时间约 {proposed.astimezone(UTC).strftime('%H:%M')}（UTC）；"
                "应用后会重新计算剩余冲突。"
            )
        if route_note:
            option_a_detail += route_note
        options.append(
            {
                "option_id": "A",
                "label": "调整本次时间",
                "detail": option_a_detail,
                "recommended": True,
                "proposed_start": proposed,
                "creates_todo": None,
                "shift_seconds": shift,
                "target_schedule_id": None,
                "executable": True,
                "unavailable_reason": None,
                "affected": [],
            }
        )
        # B only moves a neighbouring appointment. It cannot resolve an
        # overlap between this appointment and a long event.
        neighbour = target if worst["relation"] != "overlap" else None
        if neighbour is not None and neighbour["schedule_id"]:
            needed = abs(shift) if shift else min_gap_seconds
            neighbour_shift = -needed if direction > 0 else needed
            affected_start = neighbour["start"] + timedelta(seconds=neighbour_shift)
            affected_end = neighbour["end"] + timedelta(seconds=neighbour_shift)
            affected = [
                {
                    "type": "schedule",
                    "matter_id": neighbour.get("matter_id"),
                    "matter_title": neighbour.get("matter_title"),
                    "schedule_id": neighbour["schedule_id"],
                    "title": neighbour["title"],
                    "source": neighbour.get("source"),
                    "before_starts_at": _iso(neighbour["start"]),
                    "before_ends_at": _iso(neighbour["end"]),
                    "after_starts_at": _iso(affected_start),
                    "after_ends_at": _iso(affected_end),
                }
            ]
            editable = bool(neighbour.get("editable"))
            matter_label = (
                f"事项「{neighbour.get('matter_title')}」中的"
                if neighbour.get("matter_title")
                else ""
            )
            options.append(
                {
                    "option_id": "B",
                    "label": "调整相邻安排",
                    "detail": (
                        f"把{matter_label}「{neighbour['title']}」"
                        + ("提前" if direction > 0 else "顺延")
                        + f"约 {max(5, needed // 60)} 分钟，并生成一条联系待办。"
                        + (
                            ""
                            if editable
                            else "该相邻日程来自只读副本，不能在应用内调整。"
                        )
                    ),
                    "recommended": False,
                    "proposed_start": None,
                    "creates_todo": (
                        f"联系相关方调整「{neighbour['title']}」时间"
                        if editable
                        else None
                    ),
                    "shift_seconds": neighbour_shift,
                    "target_schedule_id": neighbour["schedule_id"],
                    "executable": editable,
                    "unavailable_reason": (
                        None
                        if editable
                        else "相邻日程来自苹果端只读副本，不能在应用内调整。"
                    ),
                    "affected": affected,
                }
            )
        options.append(
            {
                "option_id": "C",
                "label": "保持时间，提前出发",
                "detail": "按原时间进行，提前结束上一项或自行确认路况；系统不会替你改外部安排。",
                "recommended": False,
                "proposed_start": None,
                "creates_todo": None,
                "shift_seconds": 0,
                "target_schedule_id": None,
                "executable": True,
                "unavailable_reason": None,
                "affected": [],
            }
        )

    note = None
    if eta_notes:
        note = "travel_unverified:" + ",".join(sorted(set(eta_notes)))
    return {
        "has_conflict": bool(conflicts),
        "severity": severity,
        "window": {"start": _iso(window_start), "end": _iso(window_end)},
        "conflicts": conflicts,
        "options": options,
        "eta_note": note,
    }


__all__ = ["can_edit_schedule", "evaluate_conflict"]
