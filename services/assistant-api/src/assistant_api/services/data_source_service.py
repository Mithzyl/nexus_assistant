"""Source registration, scoped uploads, authorization and user-isolated reads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import (
    ContentItem,
    ContentSource,
    ContentSourceScope,
    ContentSyncBatch,
)
from assistant_api.schemas.data_sources import (
    FIELDS_FOR_SOURCE,
    KIND_FOR_SOURCE,
    ContentItemRead,
    ContentListResponse,
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    SyncItem,
    SyncRequest,
    SyncResponse,
)

# App-facing authorization names -> ContentItem columns.
FIELD_COLUMNS: dict[str, str] = {
    "title": "title",
    "body": "body",
    "notes": "body",
    "start": "starts_at",
    "end": "ends_at",
    "due": "due_at",
    "location": "location",
    "status": "status",
    "tags": "tags_json",
}
AUTH_FIELD_FOR_ITEM_FIELD = {
    "starts_at": "start",
    "ends_at": "end",
    "due_at": "due",
    "body": "body",
    "notes": "notes",
    "title": "title",
    "location": "location",
    "status": "status",
    "tags": "tags",
}
REQUIRED_FIELDS = {
    "calendar": {"title", "start"},
    "reminders": {"title"},
    "notes": {"title"},
}


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _mask(source: ContentSource) -> set[str]:
    return set(source.upload_fields_json or [])


def _validate_fields(source_type: str, fields: list[str]) -> None:
    if not set(fields) <= FIELDS_FOR_SOURCE[source_type]:
        raise InvalidRequestError(
            "upload_fields_invalid", "上传字段不适用于该数据来源。"
        )
    missing = REQUIRED_FIELDS[source_type] - set(fields)
    if missing:
        raise InvalidRequestError(
            "upload_fields_incomplete",
            "必须至少授权 " + ", ".join(sorted(missing)) + "。",
        )


async def _active_scopes(db: AsyncSession, source_id: str) -> list[str]:
    rows = await db.scalars(
        select(ContentSourceScope.scope_key)
        .where(ContentSourceScope.source_id == source_id, ContentSourceScope.active.is_(True))
        .order_by(ContentSourceScope.scope_key)
    )
    return list(rows)


async def _replace_scopes(
    db: AsyncSession, source: ContentSource, requested: list[str]
) -> None:
    """Deactivate omissions; re-adds start a new activation window."""

    existing = {
        row.scope_key: row
        for row in await db.scalars(
            select(ContentSourceScope).where(ContentSourceScope.source_id == source.source_id)
        )
    }
    now = _now()
    wanted = set(requested)
    for key, row in existing.items():
        if key not in wanted and row.active:
            row.active = False
            row.updated_at = now
    for key in requested:
        row = existing.get(key)
        if row is None:
            db.add(
                ContentSourceScope(
                    source_id=source.source_id,
                    scope_key=key,
                    active=True,
                    activated_at=now,
                )
            )
        elif not row.active:
            row.active = True
            row.activated_at = now
            row.updated_at = now
    await db.flush()


async def _item_count(db: AsyncSession, source_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(ContentItem).where(
                ContentItem.source_id == source_id, ContentItem.deleted_at.is_(None)
            )
        )
        or 0
    )


async def _to_source_read(db: AsyncSession, source: ContentSource) -> DataSourceRead:
    scopes = await _active_scopes(db, source.source_id)
    return DataSourceRead(
        source_id=source.source_id,
        source_type=source.source_type,
        device_id=source.device_id,
        display_name=source.display_name,
        sync_enabled=source.sync_enabled,
        assistant_enabled=source.assistant_enabled,
        upload_fields=list(source.upload_fields_json or []),
        scope_keys=scopes,
        last_sync_at=source.last_sync_at,
        last_sync_status=source.last_sync_status,
        last_error_code=source.last_error_code,
        item_count=await _item_count(db, source.source_id),
    )


async def get_source(
    db: AsyncSession, user_id: str, source_id: str, *, lock: bool = False
) -> ContentSource:
    statement = select(ContentSource).where(
        ContentSource.source_id == source_id, ContentSource.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update()
    source = await db.scalar(statement)
    if source is None:
        raise NotFoundError("data_source_not_found", "数据来源不存在。")
    return source


async def list_sources(db: AsyncSession, user_id: str) -> list[DataSourceRead]:
    rows = await db.scalars(
        select(ContentSource)
        .where(ContentSource.user_id == user_id)
        .order_by(ContentSource.created_at.desc())
    )
    return [await _to_source_read(db, row) for row in rows]


async def create_source(
    db: AsyncSession, user_id: str, payload: DataSourceCreate
) -> DataSourceRead:
    _validate_fields(payload.source_type, payload.upload_fields)
    existing = await db.scalar(
        select(ContentSource).where(
            ContentSource.user_id == user_id,
            ContentSource.device_id == payload.device_id,
            ContentSource.source_type == payload.source_type,
            ContentSource.display_name == payload.display_name,
        )
    )
    if existing is not None:
        raise ConflictError("data_source_exists", "该设备来源已登记。")
    source = ContentSource(
        user_id=user_id,
        source_type=payload.source_type,
        device_id=payload.device_id,
        display_name=payload.display_name,
        sync_enabled=payload.sync_enabled,
        assistant_enabled=payload.assistant_enabled,
        upload_fields_json=list(payload.upload_fields),
    )
    db.add(source)
    await db.flush()
    await _replace_scopes(db, source, payload.scope_keys)
    await db.commit()
    await db.refresh(source)
    return await _to_source_read(db, source)


async def update_source(
    db: AsyncSession, user_id: str, source_id: str, payload: DataSourceUpdate
) -> DataSourceRead:
    source = await get_source(db, user_id, source_id, lock=True)
    if payload.display_name is not None:
        source.display_name = payload.display_name
    if payload.sync_enabled is not None:
        source.sync_enabled = payload.sync_enabled
    if payload.assistant_enabled is not None:
        source.assistant_enabled = payload.assistant_enabled
    if payload.upload_fields is not None:
        _validate_fields(source.source_type, payload.upload_fields)
        source.upload_fields_json = list(payload.upload_fields)
        await _strip_unauthorized_fields(db, source)
    if payload.scope_keys is not None:
        await _replace_scopes(db, source, payload.scope_keys)
    await db.commit()
    await db.refresh(source)
    return await _to_source_read(db, source)


async def _strip_unauthorized_fields(db: AsyncSession, source: ContentSource) -> None:
    """Revoking a field must remove old copies, not only hide new uploads."""

    allowed = _mask(source)
    rows = await db.scalars(
        select(ContentItem).where(ContentItem.source_id == source.source_id)
    )
    for item in rows:
        if "title" not in allowed:
            item.title = ""
        if not ({"body", "notes"} & allowed):
            item.body = ""
        if "start" not in allowed:
            item.starts_at = None
        if "end" not in allowed:
            item.ends_at = None
        if "due" not in allowed:
            item.due_at = None
        if "location" not in allowed:
            item.location = None
        if "status" not in allowed:
            item.status = None
        if "tags" not in allowed:
            item.tags_json = []
        item.payload_json = {
            key: value for key, value in (item.payload_json or {}).items() if key in allowed
        }
    await db.flush()


def _item_values(source: ContentSource, item: SyncItem) -> dict[str, Any]:
    allowed = _mask(source)
    for field in item.model_fields_set:
        if field in {"external_id", "scope_key", "version", "updated_at"}:
            continue
        auth_field = AUTH_FIELD_FOR_ITEM_FIELD.get(field, field)
        if auth_field not in allowed:
            raise InvalidRequestError(
                "field_not_authorized", f"字段 {auth_field} 未授权上传。"
            )
    required = REQUIRED_FIELDS[source.source_type]
    provided = {
        AUTH_FIELD_FOR_ITEM_FIELD.get(field, field) for field in item.model_fields_set
    } & set(FIELD_COLUMNS)
    if not required <= provided:
        raise InvalidRequestError("sync_item_incomplete", "同步项缺少必要字段。")
    if "title" in required and not (item.title or "").strip():
        raise InvalidRequestError("sync_item_incomplete", "同步项缺少标题。")
    if "start" in required and item.starts_at is None:
        raise InvalidRequestError("sync_item_incomplete", "日程缺少开始时间。")

    values: dict[str, Any] = {
        "title": "",
        "body": "",
        "starts_at": None,
        "ends_at": None,
        "due_at": None,
        "status": None,
        "location": None,
        "tags_json": [],
        "payload_json": {},
    }
    mapping = {
        "title": item.title,
        "body": item.body,
        "notes": item.notes,
        "starts_at": item.starts_at,
        "ends_at": item.ends_at,
        "due_at": item.due_at,
        "status": item.status,
        "location": item.location,
        "tags_json": item.tags,
    }
    for field in allowed:
        column = FIELD_COLUMNS[field]
        value = item.notes if field == "notes" else mapping[column]
        if value is None:
            value = [] if column == "tags_json" else None
        values[column] = value
        if column != "body" or value:
            payload_value = value.isoformat() if isinstance(value, datetime) else value
            values["payload_json"][field] = payload_value
    return values


async def sync_source(
    db: AsyncSession, user_id: str, source_id: str, payload: SyncRequest
) -> SyncResponse:
    source = await get_source(db, user_id, source_id, lock=True)
    if not source.sync_enabled:
        raise ConflictError("sync_disabled", "该数据来源已停止同步。")
    batch = await db.scalar(
        select(ContentSyncBatch).where(
            ContentSyncBatch.source_id == source_id,
            ContentSyncBatch.batch_id == payload.batch_id,
        )
    )
    if batch is not None:
        return SyncResponse(
            batch_id=batch.batch_id,
            created=batch.created_count,
            updated=batch.updated_count,
            unchanged=batch.unchanged_count,
            removed=batch.removed_count,
            last_sync_at=source.last_sync_at or batch.created_at,
        )

    try:
        active = set(await _active_scopes(db, source_id))
        seen: set[str] = set()
        prepared: list[tuple[SyncItem, dict[str, Any]]] = []
        for item in payload.items:
            if item.external_id in seen:
                raise InvalidRequestError("duplicate_external_id", "同步批次包含重复记录。")
            seen.add(item.external_id)
            if item.scope_key not in active:
                raise InvalidRequestError("scope_not_selected", "同步项来自未选中的范围。")
            if item.ends_at is not None and item.starts_at is not None and item.ends_at < item.starts_at:
                raise InvalidRequestError("invalid_time_range", "结束时间早于开始时间。")
            prepared.append((item, _item_values(source, item)))
    except InvalidRequestError as error:
        source.last_sync_status = "failed"
        source.last_error_code = error.code
        await db.commit()
        raise

    kind = KIND_FOR_SOURCE[source.source_type]
    existing = {
        row.external_id: row
        for row in await db.scalars(
            select(ContentItem).where(
                ContentItem.source_id == source_id, ContentItem.kind == kind
            )
        )
    }
    created = updated = unchanged = removed = 0
    now = _now()
    for item, values in prepared:
        row = existing.get(item.external_id)
        incoming_updated = _as_utc(item.updated_at)
        if row is not None and (
            item.version < row.version
            or (item.version == row.version and incoming_updated <= _as_utc(row.source_updated_at))
        ):
            unchanged += 1
            continue
        if row is None:
            row = ContentItem(
                user_id=user_id,
                source_id=source_id,
                kind=kind,
                external_id=item.external_id,
                scope_key=item.scope_key,
                version=item.version,
                source_updated_at=incoming_updated,
                synced_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            created += 1
        else:
            row.scope_key = item.scope_key
            row.version = item.version
            row.source_updated_at = incoming_updated
            row.synced_at = now
            row.deleted_at = None
            updated += 1
        for key, value in values.items():
            setattr(row, key, value)

    if payload.removed_external_ids:
        rows = await db.scalars(
            select(ContentItem).where(
                ContentItem.source_id == source_id,
                ContentItem.kind == kind,
                ContentItem.external_id.in_(payload.removed_external_ids),
                ContentItem.deleted_at.is_(None),
            )
        )
        for row in rows:
            row.deleted_at = now
            removed += 1

    source.last_sync_at = now
    source.last_sync_status = "ok"
    source.last_error_code = None
    batch = ContentSyncBatch(
        source_id=source_id,
        batch_id=payload.batch_id,
        created_count=created,
        updated_count=updated,
        unchanged_count=unchanged,
        removed_count=removed,
    )
    db.add(batch)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        batch = await db.scalar(
            select(ContentSyncBatch).where(
                ContentSyncBatch.source_id == source_id,
                ContentSyncBatch.batch_id == payload.batch_id,
            )
        )
        if batch is None:
            raise
        return SyncResponse(
            batch_id=batch.batch_id,
            created=batch.created_count,
            updated=batch.updated_count,
            unchanged=batch.unchanged_count,
            removed=batch.removed_count,
            last_sync_at=source.last_sync_at or batch.created_at,
        )
    return SyncResponse(
        batch_id=payload.batch_id,
        created=created,
        updated=updated,
        unchanged=unchanged,
        removed=removed,
        last_sync_at=now,
    )


def _content_read(source: ContentSource, item: ContentItem) -> ContentItemRead:
    allowed = _mask(source)
    return ContentItemRead(
        item_id=item.item_id,
        source_id=source.source_id,
        source_type=source.source_type,
        source_display_name=source.display_name,
        kind=item.kind,
        external_id=item.external_id,
        scope_key=item.scope_key,
        version=item.version,
        title=item.title if "title" in allowed else "",
        body=item.body if ({"body", "notes"} & allowed) else "",
        starts_at=item.starts_at if "start" in allowed else None,
        ends_at=item.ends_at if "end" in allowed else None,
        due_at=item.due_at if "due" in allowed else None,
        status=item.status if "status" in allowed else None,
        location=item.location if "location" in allowed else None,
        tags=list(item.tags_json or []) if "tags" in allowed else [],
        source_updated_at=item.source_updated_at,
        synced_at=item.synced_at,
    )


def _visible_statement(user_id: str):
    return (
        select(ContentItem, ContentSource)
        .join(ContentSource, ContentSource.source_id == ContentItem.source_id)
        .join(
            ContentSourceScope,
            (ContentSourceScope.source_id == ContentItem.source_id)
            & (ContentSourceScope.scope_key == ContentItem.scope_key),
        )
        .where(
            ContentItem.user_id == user_id,
            ContentItem.deleted_at.is_(None),
            ContentSource.user_id == user_id,
            ContentSourceScope.active.is_(True),
            ContentItem.synced_at >= ContentSourceScope.activated_at,
        )
    )


async def list_content(
    db: AsyncSession,
    user_id: str,
    *,
    kind: str | None,
    query: str | None,
    limit: int,
    offset: int,
) -> ContentListResponse:
    statement = _visible_statement(user_id)
    if kind:
        statement = statement.where(ContentItem.kind == kind)
    if query:
        term = f"%{query}%"
        statement = statement.where(
            or_(ContentItem.title.like(term), ContentItem.body.like(term))
        )
    rows = (
        await db.execute(
            statement.order_by(ContentItem.source_updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return ContentListResponse(
        items=[_content_read(source, item) for item, source in rows]
    )


async def get_content(db: AsyncSession, user_id: str, item_id: str) -> ContentItemRead:
    row = (
        await db.execute(
            _visible_statement(user_id).where(ContentItem.item_id == item_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("content_not_found", "内容不存在。")
    return _content_read(row[1], row[0])


async def assistant_content(
    db: AsyncSession, user_id: str, kind: str, limit: int = 50
) -> list[tuple[ContentItem, ContentSource]]:
    rows = await db.execute(
        _visible_statement(user_id)
        .where(
            ContentItem.kind == kind,
            ContentSource.assistant_enabled.is_(True),
        )
        .order_by(ContentItem.source_updated_at.desc())
        .limit(limit)
    )
    return [(item, source) for item, source in rows.all()]


async def delete_items(db: AsyncSession, user_id: str, source_id: str) -> int:
    source = await get_source(db, user_id, source_id, lock=True)
    result = await db.execute(
        delete(ContentItem).where(ContentItem.source_id == source.source_id)
    )
    source.last_sync_status = "items_deleted"
    await db.commit()
    return int(result.rowcount or 0)


async def delete_source(db: AsyncSession, user_id: str, source_id: str) -> None:
    source = await get_source(db, user_id, source_id, lock=True)
    await db.execute(delete(ContentItem).where(ContentItem.source_id == source.source_id))
    await db.execute(
        delete(ContentSourceScope).where(ContentSourceScope.source_id == source.source_id)
    )
    await db.execute(
        delete(ContentSyncBatch).where(ContentSyncBatch.source_id == source.source_id)
    )
    await db.delete(source)
    await db.commit()
