"""Build a private ICS feed from confirmed app-native schedules only."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from icalendar import Calendar, Event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.errors import ConflictError, NotFoundError
from assistant_api.models import (
    CalendarSubscription,
    Matter,
    ScheduleEvent,
    User,
)

UID_DOMAIN = "personal-assistant.local"
UID_PREFIX = "pa-schedule-"
FEED_CALENDAR_NAME = "Personal Assistant 正式日程"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def free_busy_uid(schedule_id: str) -> str:
    return f"{UID_PREFIX}{schedule_id}@{UID_DOMAIN}"


async def _validate_matters(
    db: AsyncSession, user: User, matter_ids: list[str]
) -> list[str]:
    wanted = list(dict.fromkeys(matter_ids))
    if not wanted:
        raise ConflictError("calendar_selection_empty", "至少选择一个事项用于订阅。")
    rows = (
        await db.scalars(
            select(Matter.matter_id).where(
                Matter.user_id == user.user_id,
                Matter.matter_id.in_(wanted),
                Matter.deleted_at.is_(None),
            )
        )
    ).all()
    if set(rows) != set(wanted):
        raise NotFoundError("matter_not_found", "所选事项不存在。")
    return wanted


def _status(
    row: CalendarSubscription,
    *,
    feed_url: str | None = None,
    note: str | None = None,
) -> dict:
    return {
        "subscription_id": row.subscription_id,
        "enabled": row.enabled,
        "matter_ids": list(row.matter_ids_json or []),
        "last_read_at": row.last_read_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "feed_url": feed_url,
        "link_returned_once": feed_url is not None,
        "note": note,
    }


async def create_subscription(
    db: AsyncSession, user: User, matter_ids: list[str]
) -> dict:
    existing = await db.scalar(
        select(CalendarSubscription).where(CalendarSubscription.user_id == user.user_id)
    )
    if existing is not None:
        raise ConflictError(
            "calendar_subscription_exists",
            "订阅已存在；如需更换私密链接请重新生成。",
        )
    selected = await _validate_matters(db, user, matter_ids)
    token = secrets.token_urlsafe(32)
    row = CalendarSubscription(
        user_id=user.user_id,
        token_digest=token_digest(token),
        enabled=True,
        matter_ids_json=selected,
        rotated_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "subscription": _status(
            row,
            feed_url=None,
            note="私密链接仅在本次创建或重新生成时返回。",
        ),
        "token": token,
    }


async def rotate_subscription(
    db: AsyncSession, user: User, matter_ids: list[str]
) -> dict:
    row = await db.scalar(
        select(CalendarSubscription).where(CalendarSubscription.user_id == user.user_id)
    )
    if row is None:
        raise NotFoundError("calendar_subscription_not_found", "订阅尚未创建。")
    selected = await _validate_matters(db, user, matter_ids)
    token = secrets.token_urlsafe(32)
    row.token_digest = token_digest(token)
    row.matter_ids_json = selected
    row.enabled = True
    row.revoked_at = None
    row.rotated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    return {
        "subscription": _status(
            row,
            feed_url=None,
            note="旧链接已失效；已下载到手机的内容可能仍需手动移除。",
        ),
        "token": token,
    }


async def update_subscription(
    db: AsyncSession, user: User, *, enabled: bool | None, matter_ids: list[str] | None
) -> dict:
    row = await db.scalar(
        select(CalendarSubscription).where(CalendarSubscription.user_id == user.user_id)
    )
    if row is None:
        raise NotFoundError("calendar_subscription_not_found", "订阅尚未创建。")
    if matter_ids is not None:
        row.matter_ids_json = await _validate_matters(db, user, matter_ids)
    if enabled is not None:
        if enabled and row.revoked_at is not None:
            raise ConflictError(
                "calendar_subscription_revoked",
                "订阅已撤销，请重新生成链接。",
            )
        row.enabled = enabled
        row.revoked_at = datetime.now(UTC) if not enabled else None
    await db.commit()
    await db.refresh(row)
    return _status(
        row,
        note=(
            "订阅已关闭；已下载到手机的内容可能仍需用户自行移除。"
            if enabled is False
            else None
        ),
    )


async def get_subscription(db: AsyncSession, user: User) -> dict:
    row = await db.scalar(
        select(CalendarSubscription).where(CalendarSubscription.user_id == user.user_id)
    )
    return {"exists": row is not None, "subscription": _status(row) if row else None}


async def revoke_subscription(db: AsyncSession, user: User) -> dict:
    row = await db.scalar(
        select(CalendarSubscription).where(CalendarSubscription.user_id == user.user_id)
    )
    if row is None:
        raise NotFoundError("calendar_subscription_not_found", "订阅尚未创建。")
    row.enabled = False
    row.revoked_at = datetime.now(UTC)
    row.token_digest = token_digest(secrets.token_urlsafe(32))
    await db.commit()
    await db.refresh(row)
    return _status(
        row,
        note="订阅链接已撤销；已下载到手机的内容可能仍需用户自行移除。",
    )


async def read_feed(db: AsyncSession, token: str) -> tuple[str, str]:
    if not 20 <= len(token) <= 128:
        raise NotFoundError("calendar_feed_not_found", "订阅链接不存在或已失效。")
    row = await db.scalar(
        select(CalendarSubscription).where(
            CalendarSubscription.token_digest == token_digest(token),
            CalendarSubscription.enabled.is_(True),
        )
    )
    if row is None:
        raise NotFoundError("calendar_feed_not_found", "订阅链接不存在或已失效。")
    matter_ids = list(row.matter_ids_json or [])
    now = datetime.now(UTC)
    schedules = list(
        (
            await db.scalars(
                select(ScheduleEvent)
                .where(
                    ScheduleEvent.user_id == row.user_id,
                    ScheduleEvent.matter_id.in_(matter_ids),
                    ScheduleEvent.deleted_at.is_(None),
                    ScheduleEvent.status.notin_(("cancelled", "deleted")),
                    ScheduleEvent.data_source_id.is_(None),
                    ScheduleEvent.source.in_(("web_native",)),
                    ScheduleEvent.starts_at >= now - timedelta(days=180),
                    ScheduleEvent.starts_at < now + timedelta(days=730),
                )
                .order_by(ScheduleEvent.starts_at)
            )
        ).all()
    )
    calendar = Calendar()
    calendar.add("prodid", "-//Personal Assistant//Calendar Subscription//CN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", FEED_CALENDAR_NAME)
    calendar.add("x-published-ttl", "PT5M")
    stamp = datetime.now(UTC)
    for schedule in schedules:
        event = Event()
        event.add("uid", free_busy_uid(schedule.schedule_id))
        event.add("dtstamp", stamp)
        event.add("dtstart", ensure_aware(schedule.starts_at).astimezone(UTC))
        if schedule.ends_at:
            event.add("dtend", ensure_aware(schedule.ends_at).astimezone(UTC))
        event.add("last-modified", ensure_aware(schedule.updated_at).astimezone(UTC))
        event.add("sequence", int(ensure_aware(schedule.updated_at).timestamp()))
        event.add("summary", schedule.title)
        if schedule.location:
            event.add("location", schedule.location)
        event.add("status", "CONFIRMED")
        calendar.add_component(event)
    row.last_read_at = stamp
    await db.commit()
    return (
        calendar.to_ical().decode("utf-8"),
        FEED_CALENDAR_NAME,
    )


__all__ = [
    "FEED_CALENDAR_NAME",
    "UID_PREFIX",
    "create_subscription",
    "free_busy_uid",
    "get_subscription",
    "read_feed",
    "revoke_subscription",
    "rotate_subscription",
    "update_subscription",
]
