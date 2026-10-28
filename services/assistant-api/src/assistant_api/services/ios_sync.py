"""iOS snapshot upload and pending-action queue.

Authorization is enforced server-side, not trusted from the client:
- every submitted field must be inside the source's `upload_fields`;
- when the source has active scopes, every record must name an active `scope_key`;
- record identity includes the concrete data source, so two devices may reuse the
  same `external_id` without overwriting each other;
- deletion is version-checked like any other change;
- reminder status keeps its real meaning (completed/cancelled/pending).

`last_modified_at` maps to `source_updated_at`; `updated_at` stays the server edit
time; `source_version` is the incoming version (or its epoch fallback). There is
no second, contradictory timestamp column.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import (
    ContentSource,
    ContentSourceScope,
    ContentSyncBatch,
    Reminder,
    ScheduleEvent,
    User,
)
from assistant_api.schemas import matters as schemas

SOURCE_BY_TYPE = {"calendar": "ios_eventkit", "reminders": "ios_reminders"}
CALENDAR_FIELDS = {
    "title": "title",
    "starts_at": "start",
    "ends_at": "end",
    "location": "location",
    "status": "status",
    "notes": "notes",
}
REMINDER_FIELDS = {
    "title": "title",
    "due_at": "due",
    "status": "status",
    "notes": "notes",
}
SUBSCRIPTION_EVENT_UID_PREFIX = "pa-schedule-"


def _dt(value: datetime | None) -> datetime | None:
    return ensure_aware(value) if value is not None else None


def _reminder_state(status: str | None) -> str:
    value = (status or "").strip().lower()
    if value in ("completed", "complete", "done", "checked", "acknowledged"):
        return "completed"
    if value in ("deleted", "cancelled", "canceled", "removed"):
        return "cancelled"
    return "pending"


def _payload_hash(payload: schemas.IosSnapshotRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _scope_state(db: AsyncSession, source_id: str) -> tuple[bool, set[str]]:
    rows = (
        await db.execute(
            select(ContentSourceScope.scope_key, ContentSourceScope.active).where(
                ContentSourceScope.source_id == source_id
            )
        )
    ).all()
    return (bool(rows), {key for key, active in rows if active})


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

    allowed_fields = set(source.upload_fields_json or [])
    allowed_mapping = CALENDAR_FIELDS if source.source_type == "calendar" else REMINDER_FIELDS
    scopes_defined, active_scopes = await _scope_state(db, source.source_id)
    payload_hash = _payload_hash(payload)

    existing_batch = await db.scalar(
        select(ContentSyncBatch).where(
            ContentSyncBatch.source_id == source.source_id,
            ContentSyncBatch.batch_id == payload.batch_id,
        )
    )
    now = datetime.now(UTC)
    if existing_batch is not None:
        if (
            existing_batch.payload_sha256 is not None
            and existing_batch.payload_sha256 != payload_hash
        ):
            raise ConflictError(
                "batch_content_mismatch",
                "同一批次编号已用于不同内容；请换一个新的 batch_id。",
            )
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

    # Validate authorization before writing anything.
    for index, record in enumerate(payload.records):
        if (
            source.source_type == "calendar"
            and record.external_id.startswith(SUBSCRIPTION_EVENT_UID_PREFIX)
        ):
            raise InvalidRequestError(
                "subscription_echo_not_allowed",
                "快捷指令不能上传本应用生成的日历订阅事件，以免形成重复循环。",
            )
        provided = set(record.model_fields_set)
        mapped = {allowed_mapping[key] for key in provided if key in allowed_mapping}
        unauthorized = mapped - allowed_fields
        if unauthorized:
            raise InvalidRequestError(
                "upload_field_not_authorized",
                f"第 {index + 1} 条包含未授权字段：{', '.join(sorted(unauthorized))}。",
            )
        if scopes_defined and record.scope_key not in active_scopes:
            raise InvalidRequestError(
                "scope_not_authorized",
                f"第 {index + 1} 条缺少已授权的 scope_key。",
            )

    created = updated = unchanged = removed = 0
    is_calendar = source.source_type == "calendar"

    async def find_record(external_id: str):
        if is_calendar:
            return await db.scalar(
                select(ScheduleEvent).where(
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.source == label,
                    ScheduleEvent.data_source_id == source.source_id,
                    ScheduleEvent.source_external_id == external_id,
                )
            )
        return await db.scalar(
            select(Reminder).where(
                Reminder.user_id == user.user_id,
                Reminder.source == label,
                Reminder.data_source_id == source.source_id,
                Reminder.source_external_id == external_id,
            )
        )

    for index, record in enumerate(payload.records):
        incoming = _dt(record.last_modified_at)
        version = record.version or int(incoming.timestamp())
        current = await find_record(record.external_id)
        if current is not None and current.source_updated_at is not None and (
            ensure_aware(current.source_updated_at) >= incoming
        ):
            # Old delete and old update are both ignored.
            unchanged += 1
            continue
        if record.deleted:
            if current is None:
                unchanged += 1
            elif is_calendar:
                current.deleted_at = now
                current.status = "cancelled"
                current.source_updated_at = incoming
                current.source_version = version
                removed += 1
            else:
                current.status = "cancelled"
                current.cancelled_at = now
                current.deleted_at = now
                current.source_updated_at = incoming
                current.source_version = version
                removed += 1
            continue
        if is_calendar and record.starts_at is None:
            raise InvalidRequestError(
                "snapshot_record_invalid", f"第 {index + 1} 条日历记录缺少 starts_at。"
            )
        if not is_calendar and record.due_at is None:
            raise InvalidRequestError(
                "snapshot_record_invalid", f"第 {index + 1} 条提醒记录缺少 due_at。"
            )
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
                    source_scope_key=record.scope_key,
                    source_updated_at=incoming,
                    source_version=version,
                    data_source_id=source.source_id,
                )
            else:
                state = _reminder_state(record.status)
                current = Reminder(
                    user_id=user.user_id,
                    title=record.title or "（未命名提醒）",
                    note=record.notes,
                    trigger_at=_dt(record.due_at),
                    status=state,
                    completed_at=incoming if state == "completed" else None,
                    cancelled_at=incoming if state == "cancelled" else None,
                    sync_status="ios_synced",
                    source=label,
                    source_external_id=record.external_id,
                    source_scope_key=record.scope_key,
                    data_source_id=source.source_id,
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
                current.source_scope_key = record.scope_key
                current.data_source_id = source.source_id
            else:
                current.title = record.title or current.title
                current.note = record.notes
                current.trigger_at = _dt(record.due_at)
                state = _reminder_state(record.status)
                current.status = state
                current.completed_at = incoming if state == "completed" else None
                current.cancelled_at = incoming if state == "cancelled" else None
                current.deleted_at = None
                current.source_scope_key = record.scope_key
                current.data_source_id = source.source_id
            current.source_updated_at = incoming
            current.source_version = version
            updated += 1

    for external_id in payload.removed_external_ids:
        current = await find_record(external_id)
        if current is None:
            unchanged += 1
            continue
        target_time = now
        if is_calendar:
            if current.deleted_at is not None:
                unchanged += 1
                continue
            current.deleted_at = target_time
            current.status = "cancelled"
        else:
            if current.status == "cancelled":
                unchanged += 1
                continue
            current.status = "cancelled"
            current.cancelled_at = target_time
            current.deleted_at = target_time
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
            payload_sha256=payload_hash,
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
                    Reminder.deleted_at.is_(None),
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
