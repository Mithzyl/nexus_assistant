"""iOS snapshot upload and pending-action queue.

The snapshot reuses the existing source authorization model and batch idempotency:
a repeated batch is a no-op, an older `last_modified_at` never overwrites a newer
local copy, and a record absent from the batch is NOT treated as deleted.

`last_modified_at` is stored as `source_updated_at`; `updated_at` stays the server
edit time and `source_version` is the incoming version (or its epoch fallback).
There is no second, contradictory timestamp column.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import (
    ContentSource,
    ContentSyncBatch,
    Reminder,
    ScheduleEvent,
    User,
)
from assistant_api.schemas import matters as schemas

SOURCE_BY_TYPE = {"calendar": "ios_eventkit", "reminders": "ios_reminders"}


def _dt(value: datetime | None) -> datetime | None:
    return ensure_aware(value) if value is not None else None


async def apply_snapshot(
    db: AsyncSession, user: User, payload: schemas.IosSnapshotRequest
) -> dict:
    source = await db.scalar(
        select(ContentSource).where(
            ContentSource.source_id == payload.source_id,
            ContentSource.user_id == user.user_id,
        )
    )
    if source is None:
        raise NotFoundError("source_not_found", "资料来源不存在。")
    label = SOURCE_BY_TYPE.get(source.source_type)
    if label is None:
        raise InvalidRequestError(
            "source_type_not_syncable", "该来源不是日历或提醒事项，不能接收 iOS 快照。"
        )
    if not source.sync_enabled:
        raise ConflictError("source_sync_disabled", "该来源已关闭上传。")

    existing_batch = await db.scalar(
        select(ContentSyncBatch).where(
            ContentSyncBatch.source_id == source.source_id,
            ContentSyncBatch.batch_id == payload.batch_id,
        )
    )
    now = datetime.now(UTC)
    if existing_batch is not None:
        return {
            "batch_id": payload.batch_id,
            "source": label,
            "created": existing_batch.created_count,
            "updated": existing_batch.updated_count,
            "unchanged": existing_batch.unchanged_count,
            "removed": existing_batch.removed_count,
            "last_sync_at": source.last_sync_at or now,
            "replayed": True,
        }

    created = updated = unchanged = removed = 0
    is_calendar = source.source_type == "calendar"

    async def find_record(external_id: str):
        if is_calendar:
            return await db.scalar(
                select(ScheduleEvent).where(
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.source == label,
                    ScheduleEvent.source_external_id == external_id,
                )
            )
        return await db.scalar(
            select(Reminder).where(
                Reminder.user_id == user.user_id,
                Reminder.source == label,
                Reminder.source_external_id == external_id,
            )
        )

    for index, record in enumerate(payload.records):
        incoming = _dt(record.last_modified_at)
        version = record.version or int(incoming.timestamp())
        current = await find_record(record.external_id)
        if record.deleted:
            if current is not None and getattr(current, "deleted_at", None) is None and (
                current.status != "cancelled"
            ):
                if is_calendar:
                    current.deleted_at = now
                    current.status = "cancelled"
                else:
                    current.status = "cancelled"
                    current.cancelled_at = now
                removed += 1
            else:
                unchanged += 1
            continue
        if is_calendar and record.starts_at is None:
            raise InvalidRequestError(
                "snapshot_record_invalid", f"第 {index + 1} 条日历记录缺少 starts_at。"
            )
        if not is_calendar and record.due_at is None:
            raise InvalidRequestError(
                "snapshot_record_invalid", f"第 {index + 1} 条提醒记录缺少 due_at。"
            )
        if current is not None and current.source_updated_at is not None and (
            ensure_aware(current.source_updated_at) >= incoming
        ):
            unchanged += 1
            continue
        if current is None:
            if is_calendar:
                current = ScheduleEvent(
                    user_id=user.user_id,
                    title=record.title or "（未命名日程）",
                    starts_at=_dt(record.starts_at),
                    ends_at=_dt(record.ends_at),
                    location=record.location,
                    status=record.status or "scheduled",
                    source=label,
                    sync_status="ios_synced",
                    source_external_id=record.external_id,
                    source_updated_at=incoming,
                    source_version=version,
                    data_source_id=source.source_id,
                )
            else:
                current = Reminder(
                    user_id=user.user_id,
                    title=record.title or "（未命名提醒）",
                    trigger_at=_dt(record.due_at),
                    status="pending",
                    sync_status="ios_synced",
                    source=label,
                    source_external_id=record.external_id,
                    source_updated_at=incoming,
                    source_version=version,
                )
            db.add(current)
            created += 1
        else:
            if is_calendar:
                current.title = record.title or current.title
                current.starts_at = _dt(record.starts_at)
                current.ends_at = _dt(record.ends_at)
                current.location = record.location
                if record.status:
                    current.status = record.status
                current.deleted_at = None
            else:
                current.title = record.title or current.title
                current.trigger_at = _dt(record.due_at)
                current.status = "pending"
                current.cancelled_at = None
            current.source_updated_at = incoming
            current.source_version = version
            current.data_source_id = source.source_id if is_calendar else None
            updated += 1

    for external_id in payload.removed_external_ids:
        current = await find_record(external_id)
        if current is None:
            unchanged += 1
            continue
        if is_calendar:
            if current.deleted_at is not None:
                unchanged += 1
                continue
            current.deleted_at = now
            current.status = "cancelled"
        else:
            if current.status == "cancelled":
                unchanged += 1
                continue
            current.status = "cancelled"
            current.cancelled_at = now
        removed += 1

    source.last_sync_at = now
    source.last_sync_status = "ok"
    db.add(
        ContentSyncBatch(
            source_id=source.source_id,
            batch_id=payload.batch_id,
            created_count=created,
            updated_count=updated,
            unchanged_count=unchanged,
            removed_count=removed,
        )
    )
    await db.commit()
    return {
        "batch_id": payload.batch_id,
        "source": label,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "last_sync_at": now,
        "replayed": False,
    }


async def pending_actions(db: AsyncSession, user: User) -> dict:
    now = datetime.now(UTC)
    schedules = list(
        (
            await db.scalars(
                select(ScheduleEvent).where(
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.sync_status == "pending_ios",
                    ScheduleEvent.deleted_at.is_(None),
                )
            )
        ).all()
    )
    reminders = list(
        (
            await db.scalars(
                select(Reminder).where(
                    Reminder.user_id == user.user_id,
                    Reminder.sync_status == "pending_ios",
                    Reminder.status == "pending",
                )
            )
        ).all()
    )
    items = []
    for schedule in schedules:
        items.append(
            {
                "action_id": f"schedule:{schedule.schedule_id}:{schedule.updated_at}",
                "record_type": "schedule",
                "record_id": schedule.schedule_id,
                "operation": "upsert",
                "sync_status": "pending_ios",
                "payload": {
                    "title": schedule.title,
                    "starts_at": schedule.starts_at,
                    "ends_at": schedule.ends_at,
                    "location": schedule.location,
                },
                "updated_at": schedule.updated_at,
            }
        )
    for reminder in reminders:
        items.append(
            {
                "action_id": f"reminder:{reminder.reminder_id}:{reminder.updated_at}",
                "record_type": "reminder",
                "record_id": reminder.reminder_id,
                "operation": "upsert",
                "sync_status": "pending_ios",
                "payload": {
                    "title": reminder.title,
                    "trigger_at": reminder.trigger_at,
                },
                "updated_at": reminder.updated_at,
            }
        )
    items.sort(key=lambda item: item["updated_at"])
    return {"generated_at": now, "items": items}


__all__ = ["apply_snapshot", "pending_actions"]
