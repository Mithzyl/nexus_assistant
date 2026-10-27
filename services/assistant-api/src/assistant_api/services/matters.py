"""Matter lifecycle, preview/confirm, today view and iOS sync support.

The confirmation boundary is enforced here, not in the web button: formal
schedules and reminders are only written inside `confirm_draft`, after the caller
proves the preview version and (when needed) acknowledges a conflict.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.config import Settings
from assistant_api.core.errors import (
    AssistantError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
)
from assistant_api.models import (
    ContentSource,
    Matter,
    MatterChange,
    MatterDraft,
    MatterProgress,
    MatterTodo,
    NexusRun,
    Reminder,
    ScheduleEvent,
    User,
    UserProfile,
)
from assistant_api.models.base import new_id
from assistant_api.nexus_client import NexusPilotClient
from assistant_api.schemas import matters as schemas
from assistant_api.services import matter_plan, run_service
from assistant_api.services.matter_conflicts import can_edit_schedule, evaluate_conflict


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def user_timezone(db: AsyncSession, user: User, default: str = "Asia/Shanghai") -> str:
    profile = await db.get(UserProfile, user.user_id)
    timezone = (profile.timezone if profile else None) or default
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return default
    return timezone


def _jsonable(value):
    """Convert datetimes inside a payload to ISO strings for the JSON column."""

    if isinstance(value, datetime):
        return ensure_aware(value).astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _parse_dt(value, timezone: str | None = None):
    """Return UTC; SQLite stores wall-clock values without an offset.

    Naive input is anchored in the supplied timezone (the user's timezone), so a
    web edit and a model extraction interpret an offset-less value the same way.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(timezone or "UTC"))
        return ensure_aware(value).astimezone(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone or "UTC"))
        return parsed.astimezone(UTC)
    return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return ensure_aware(value).astimezone(UTC)


OPTION_TRANSIENT_KEYS = ("selected_option", "previous_adjustment", "affected")
CHANGE_SNAPSHOT_VERSION = 2


def _option_baseline(body: dict) -> dict:
    """Return a copy without the effects of a previously selected option."""

    baseline = deepcopy(body)
    for key in OPTION_TRANSIENT_KEYS:
        baseline.pop(key, None)
    return baseline


async def _get_matter(db: AsyncSession, user: User, matter_id: str) -> Matter:
    matter = await db.scalar(
        select(Matter).where(
            Matter.matter_id == matter_id,
            Matter.user_id == user.user_id,
            Matter.deleted_at.is_(None),
        )
    )
    if matter is None:
        raise NotFoundError("matter_not_found", "事项不存在。")
    return matter


async def _get_draft(db: AsyncSession, user: User, draft_id: str) -> MatterDraft:
    draft = await db.scalar(
        select(MatterDraft).where(
            MatterDraft.draft_id == draft_id,
            MatterDraft.user_id == user.user_id,
        )
    )
    if draft is None:
        raise NotFoundError("draft_not_found", "变更预览不存在。")
    return draft


def _matter_fields(matter: Matter) -> dict:
    return {
        "title": matter.title,
        "goal": matter.goal,
        "status": matter.status,
        "next_step": matter.next_step,
    }


def _schedule_snapshot(schedule: ScheduleEvent | None) -> dict | None:
    if schedule is None:
        return None
    return {
        "schedule_id": schedule.schedule_id,
        "matter_id": schedule.matter_id,
        "title": schedule.title,
        "starts_at": _as_utc(schedule.starts_at).isoformat() if schedule.starts_at else None,
        "ends_at": _as_utc(schedule.ends_at).isoformat() if schedule.ends_at else None,
        "location": schedule.location,
        "status": schedule.status,
        "source_run_id": schedule.source_run_id,
        "latitude": schedule.latitude,
        "longitude": schedule.longitude,
        "source": schedule.source,
        "sync_status": schedule.sync_status,
        "source_external_id": schedule.source_external_id,
        "source_scope_key": schedule.source_scope_key,
        "source_updated_at": (
            _as_utc(schedule.source_updated_at).isoformat()
            if schedule.source_updated_at
            else None
        ),
        "source_version": schedule.source_version,
        "data_source_id": schedule.data_source_id,
        "deleted_at": _as_utc(schedule.deleted_at).isoformat() if schedule.deleted_at else None,
        "updated_at": _as_utc(schedule.updated_at).isoformat() if schedule.updated_at else None,
    }


def _todo_snapshot(todo: MatterTodo | None) -> dict | None:
    if todo is None:
        return None
    return {
        "todo_id": todo.todo_id,
        "matter_id": todo.matter_id,
        "title": todo.title,
        "status": todo.status,
        "position": todo.position,
        "due_at": _as_utc(todo.due_at).isoformat() if todo.due_at else None,
        "reminder_at": _as_utc(todo.reminder_at).isoformat() if todo.reminder_at else None,
        "completed_at": _as_utc(todo.completed_at).isoformat() if todo.completed_at else None,
        "updated_at": _as_utc(todo.updated_at).isoformat() if todo.updated_at else None,
    }


def _reminder_snapshot(reminder: Reminder | None) -> dict | None:
    if reminder is None:
        return None
    return {
        "reminder_id": reminder.reminder_id,
        "matter_id": reminder.matter_id,
        "todo_id": reminder.todo_id,
        "title": reminder.title,
        "note": reminder.note,
        "trigger_at": _as_utc(reminder.trigger_at).isoformat() if reminder.trigger_at else None,
        "status": reminder.status,
        "sync_status": reminder.sync_status,
        "source": reminder.source,
        "source_external_id": reminder.source_external_id,
        "source_scope_key": reminder.source_scope_key,
        "data_source_id": reminder.data_source_id,
        "source_updated_at": (
            _as_utc(reminder.source_updated_at).isoformat()
            if reminder.source_updated_at
            else None
        ),
        "source_version": reminder.source_version,
        "completed_at": _as_utc(reminder.completed_at).isoformat() if reminder.completed_at else None,
        "cancelled_at": _as_utc(reminder.cancelled_at).isoformat() if reminder.cancelled_at else None,
        "result_json": reminder.result_json,
        "updated_at": _as_utc(reminder.updated_at).isoformat() if reminder.updated_at else None,
    }


def _snap_dt(value: str | None) -> datetime | None:
    return _parse_dt(value) if value else None


def _schedule_matches(schedule: ScheduleEvent | None, snapshot: dict) -> bool:
    if schedule is None or snapshot is None:
        return False
    if snapshot.get("updated_at") and _as_utc(schedule.updated_at) != _snap_dt(
        snapshot["updated_at"]
    ):
        return False
    return (
        schedule.title == snapshot.get("title")
        and _as_utc(schedule.starts_at) == _snap_dt(snapshot.get("starts_at"))
        and _as_utc(schedule.ends_at) == _snap_dt(snapshot.get("ends_at"))
        and schedule.location == snapshot.get("location")
        and schedule.deleted_at is None
    )


def _todo_matches(todo: MatterTodo | None, snapshot: dict) -> bool:
    if todo is None or snapshot is None:
        return False
    if snapshot.get("updated_at") and _as_utc(todo.updated_at) != _snap_dt(
        snapshot["updated_at"]
    ):
        return False
    return (
        todo.title == snapshot.get("title")
        and todo.status == snapshot.get("status")
        and _as_utc(todo.due_at) == _snap_dt(snapshot.get("due_at"))
        and _as_utc(todo.reminder_at) == _snap_dt(snapshot.get("reminder_at"))
        and _as_utc(todo.completed_at) == _snap_dt(snapshot.get("completed_at"))
    )


def _reminder_matches(reminder: Reminder | None, snapshot: dict) -> bool:
    if reminder is None or snapshot is None:
        return False
    if snapshot.get("updated_at") and _as_utc(reminder.updated_at) != _snap_dt(
        snapshot["updated_at"]
    ):
        return False
    return (
        reminder.title == snapshot.get("title")
        and _as_utc(reminder.trigger_at) == _snap_dt(snapshot.get("trigger_at"))
        and reminder.status == snapshot.get("status")
        and _as_utc(reminder.completed_at) == _snap_dt(snapshot.get("completed_at"))
        and _as_utc(reminder.cancelled_at) == _snap_dt(snapshot.get("cancelled_at"))
    )


MATTER_SNAPSHOT_KEYS = frozenset({"title", "goal", "status", "next_step"})
SCHEDULE_SNAPSHOT_KEYS = frozenset(
    {
        "schedule_id",
        "matter_id",
        "title",
        "starts_at",
        "ends_at",
        "location",
        "status",
        "source_run_id",
        "source",
        "sync_status",
        "source_external_id",
        "source_scope_key",
        "source_updated_at",
        "source_version",
        "data_source_id",
        "latitude",
        "longitude",
        "deleted_at",
        "updated_at",
    }
)
TODO_SNAPSHOT_KEYS = frozenset(
    {
        "todo_id",
        "matter_id",
        "title",
        "status",
        "position",
        "due_at",
        "reminder_at",
        "completed_at",
        "updated_at",
    }
)
REMINDER_SNAPSHOT_KEYS = frozenset(
    {
        "reminder_id",
        "matter_id",
        "todo_id",
        "title",
        "note",
        "trigger_at",
        "status",
        "sync_status",
        "source",
        "source_external_id",
        "source_scope_key",
        "data_source_id",
        "source_updated_at",
        "source_version",
        "completed_at",
        "cancelled_at",
        "result_json",
        "updated_at",
    }
)


def _snapshot_complete(snapshot, keys: frozenset[str]) -> bool:
    return isinstance(snapshot, dict) and keys.issubset(snapshot)


def _change_snapshot_incomplete(before: dict, after: dict) -> bool:
    """Reject legacy changes that cannot prove a complete before/after restore."""

    if not _snapshot_complete(before.get("matter"), MATTER_SNAPSHOT_KEYS):
        return True
    if not _snapshot_complete(after.get("matter"), MATTER_SNAPSHOT_KEYS):
        return True
    if "matter_version" not in after:
        return True

    after_schedule = after.get("schedule")
    if after_schedule is not None:
        if "schedule" not in before or not _snapshot_complete(
            after_schedule, SCHEDULE_SNAPSHOT_KEYS
        ):
            return True
        before_schedule = before.get("schedule")
        if before_schedule is not None and not _snapshot_complete(
            before_schedule, SCHEDULE_SNAPSHOT_KEYS
        ):
            return True

    collection_specs = (
        (
            "adjacent_schedules",
            SCHEDULE_SNAPSHOT_KEYS,
            "schedule_id",
            None,
        ),
        ("todos", TODO_SNAPSHOT_KEYS, "todo_id", "completed_todo_ids"),
        ("reminders", REMINDER_SNAPSHOT_KEYS, "reminder_id", "reminder_ids"),
    )
    for collection, keys, id_key, legacy_ids in collection_specs:
        entries = after.get(collection)
        if entries is None:
            # Older rows may omit an unchanged collection. Callers reject the
            # separate legacy id lists below when the change actually touched it.
            continue
        if not isinstance(entries, list):
            return True
        if collection not in before or not isinstance(before.get(collection), list):
            return True
        before_by_id = {
            item.get(id_key): item
            for item in before.get(collection) or []
            if isinstance(item, dict) and item.get(id_key)
        }
        for item in entries:
            if not _snapshot_complete(item, keys):
                return True
            prior = before_by_id.get(item.get(id_key))
            if prior is not None and not _snapshot_complete(prior, keys):
                return True
        touched_ids = set(after.get(legacy_ids) or []) if legacy_ids else set()
        if touched_ids - {
            item.get(id_key)
            for item in entries
            if isinstance(item, dict)
        }:
            return True

    # Old snapshots recorded only these id lists. A non-empty list without its
    # matching after snapshot cannot prove what the complete previous state was.
    if after.get("completed_todo_ids") and not after.get("todos"):
        return True
    if after.get("reminder_ids") and not after.get("reminders"):
        return True
    return False


def _affected_matter_ids(after: dict) -> list[str]:
    return sorted(
        {
            item["matter_id"]
            for key in ("adjacent_schedules", "affected")
            for item in after.get(key) or []
            if isinstance(item, dict) and item.get("matter_id")
        }
    )


def _serialize_schedule(schedule: ScheduleEvent) -> dict:
    return {
        "schedule_id": schedule.schedule_id,
        "matter_id": schedule.matter_id,
        "title": schedule.title,
        "starts_at": schedule.starts_at,
        "ends_at": schedule.ends_at,
        "location": schedule.location,
        "latitude": schedule.latitude,
        "longitude": schedule.longitude,
        "status": schedule.status,
        "source": schedule.source,
        "sync_status": schedule.sync_status,
        "deleted_at": schedule.deleted_at,
    }


def _serialize_reminder(reminder: Reminder) -> dict:
    return {
        "reminder_id": reminder.reminder_id,
        "matter_id": reminder.matter_id,
        "todo_id": reminder.todo_id,
        "title": reminder.title,
        "note": reminder.note,
        "trigger_at": reminder.trigger_at,
        "status": reminder.status,
        "sync_status": reminder.sync_status,
        "source": reminder.source,
        "completed_at": reminder.completed_at,
        "cancelled_at": reminder.cancelled_at,
    }


def _serialize_todo(todo: MatterTodo) -> dict:
    return {
        "todo_id": todo.todo_id,
        "title": todo.title,
        "status": todo.status,
        "position": todo.position,
        "due_at": todo.due_at,
        "reminder_at": todo.reminder_at,
        "completed_at": todo.completed_at,
    }


async def _serialize_matters(
    db: AsyncSession, matters: list[Matter]
) -> list[dict]:
    if not matters:
        return []
    ids = [matter.matter_id for matter in matters]
    todo_rows = (
        await db.scalars(
            select(MatterTodo).where(MatterTodo.matter_id.in_(ids))
        )
    ).all()
    open_counts: dict[str, int] = {}
    for todo in todo_rows:
        if todo.status == "open":
            open_counts[todo.matter_id] = open_counts.get(todo.matter_id, 0) + 1
    progress_rows = (
        await db.scalars(
            select(MatterProgress)
            .where(MatterProgress.matter_id.in_(ids))
            .order_by(MatterProgress.created_at.desc())
        )
    ).all()
    latest: dict[str, str] = {}
    for progress in progress_rows:
        latest.setdefault(progress.matter_id, progress.text)
    return [
        {
            "matter_id": matter.matter_id,
            "title": matter.title,
            "goal": matter.goal,
            "status": matter.status,
            "version": matter.version,
            "next_step": matter.next_step,
            "last_progress_at": matter.last_progress_at,
            "latest_progress": latest.get(matter.matter_id),
            "open_todos": open_counts.get(matter.matter_id, 0),
            "created_at": matter.created_at,
            "updated_at": matter.updated_at,
        }
        for matter in matters
    ]


def _serialize_draft(draft: MatterDraft) -> dict:
    return {
        "draft_id": draft.draft_id,
        "kind": draft.kind,
        "status": draft.status,
        "version": draft.version,
        "matter_id": draft.matter_id,
        "payload": draft.payload_json or {},
        "conflicts": draft.conflict_json,
        "candidates": draft.candidates_json,
        "ai_run_id": draft.ai_run_id,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


async def _conflict_for_payload(
    db: AsyncSession,
    runtime: CommuteRuntime,
    user: User,
    payload: dict,
    *,
    settings: Settings,
    timezone: str,
    exclude_schedule_id: str | None = None,
    adjusted: dict[str, tuple[datetime, datetime | None]] | None = None,
) -> dict | None:
    schedule = payload.get("schedule")
    if not schedule:
        return None
    start = _parse_dt(schedule["starts_at"], timezone)
    end = _parse_dt(schedule.get("ends_at"), timezone)
    return await evaluate_conflict(
        db,
        runtime,
        user=user,
        starts_at=start,
        ends_at=end,
        latitude=schedule.get("latitude"),
        longitude=schedule.get("longitude"),
        exclude_schedule_id=exclude_schedule_id,
        window_hours=settings.matter_conflict_window_hours,
        min_gap_seconds=settings.matter_conflict_min_gap_seconds,
        adjusted=adjusted,
    )


# ---------------------------------------------------------------------------
# matter read/write
# ---------------------------------------------------------------------------


async def list_matters(
    db: AsyncSession, user: User, *, group: str, limit: int, offset: int
) -> tuple[list[dict], str | None]:
    statement = select(Matter).where(
        Matter.user_id == user.user_id, Matter.deleted_at.is_(None)
    )
    if group != "all":
        statement = statement.where(Matter.status == group)
    statement = (
        statement.order_by(Matter.updated_at.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    rows = list((await db.scalars(statement)).all())
    has_more = len(rows) > limit
    items = await _serialize_matters(db, rows[:limit])
    return items, str(offset + limit) if has_more else None


async def get_matter_detail(db: AsyncSession, user: User, matter_id: str) -> dict:
    matter = await _get_matter(db, user, matter_id)
    base = (await _serialize_matters(db, [matter]))[0]
    schedules = list(
        (
            await db.scalars(
                select(ScheduleEvent)
                .where(
                    ScheduleEvent.matter_id == matter.matter_id,
                    ScheduleEvent.deleted_at.is_(None),
                )
                .order_by(ScheduleEvent.starts_at)
            )
        ).all()
    )
    todos = list(
        (
            await db.scalars(
                select(MatterTodo)
                .where(MatterTodo.matter_id == matter.matter_id)
                .order_by(MatterTodo.position, MatterTodo.created_at)
            )
        ).all()
    )
    progress = list(
        (
            await db.scalars(
                select(MatterProgress)
                .where(MatterProgress.matter_id == matter.matter_id)
                .order_by(MatterProgress.created_at.desc())
            )
        ).all()
    )
    reminders = list(
        (
            await db.scalars(
                select(Reminder)
                .where(Reminder.matter_id == matter.matter_id)
                .order_by(Reminder.trigger_at)
            )
        ).all()
    )
    base.update(
        {
            "schedules": [_serialize_schedule(row) for row in schedules],
            "todos": [_serialize_todo(row) for row in todos],
            "progress": [
                {
                    "progress_id": row.progress_id,
                    "text": row.text,
                    "kind": row.kind,
                    "created_at": row.created_at,
                }
                for row in progress
            ],
            "reminders": [_serialize_reminder(row) for row in reminders],
        }
    )
    return base


async def create_matter(
    db: AsyncSession, user: User, payload: schemas.MatterCreate
) -> dict:
    matter = Matter(
        user_id=user.user_id,
        title=payload.title.strip(),
        goal=(payload.goal or "").strip(),
        next_step=payload.next_step,
        status="active",
    )
    db.add(matter)
    await db.commit()
    await db.refresh(matter)
    return (await _serialize_matters(db, [matter]))[0]


async def update_matter(
    db: AsyncSession, user: User, matter_id: str, payload: schemas.MatterUpdate
) -> dict:
    matter = await _get_matter(db, user, matter_id)
    if matter.version != payload.expected_version:
        raise ConflictError("matter_changed", "事项已被其他操作修改，请刷新后再试。")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for key in ("title", "goal", "status", "next_step"):
        value = changes.get(key)
        if value is None:
            continue
        if key == "title":
            matter.title = value.strip()
        elif key == "goal":
            matter.goal = value
        else:
            setattr(matter, key, value)
    matter.version += 1
    await db.commit()
    await db.refresh(matter)
    return (await _serialize_matters(db, [matter]))[0]


async def set_status(
    db: AsyncSession,
    user: User,
    matter_id: str,
    *,
    status: str,
    expected_version: int,
    cancel_reminders: bool = False,
) -> dict:
    matter = await _get_matter(db, user, matter_id)
    if matter.version != expected_version:
        raise ConflictError("matter_changed", "事项已被其他操作修改，请刷新后再试。")
    matter.status = status
    matter.version += 1
    matter.archived_at = datetime.now(UTC) if status == "archived" else None
    if cancel_reminders:
        now = datetime.now(UTC)
        await db.execute(
            update(Reminder)
            .where(
                Reminder.matter_id == matter.matter_id,
                Reminder.status == "pending",
            )
            .values(status="cancelled", cancelled_at=now)
        )
    await db.commit()
    await db.refresh(matter)
    return (await _serialize_matters(db, [matter]))[0]


async def delete_matter(db: AsyncSession, user: User, matter_id: str) -> dict:
    matter = await _get_matter(db, user, matter_id)
    now = datetime.now(UTC)
    matter.status = "deleted"
    matter.deleted_at = now
    matter.version += 1
    await db.execute(
        update(ScheduleEvent)
        .where(ScheduleEvent.matter_id == matter.matter_id)
        .values(status="cancelled", deleted_at=now)
    )
    await db.execute(
        update(Reminder)
        .where(Reminder.matter_id == matter.matter_id, Reminder.status == "pending")
        .values(status="cancelled", cancelled_at=now)
    )
    await db.execute(
        update(MatterDraft)
        .where(MatterDraft.matter_id == matter.matter_id, MatterDraft.status == "preview")
        .values(status="cancelled", cancelled_at=now)
    )
    await db.commit()
    return {"matter_id": matter.matter_id, "deleted_at": now}


async def complete_todo(
    db: AsyncSession,
    user: User,
    matter_id: str,
    todo_id: str,
    *,
    done: bool,
    cancel_reminders: bool = False,
) -> dict:
    await _get_matter(db, user, matter_id)
    todo = await db.scalar(
        select(MatterTodo).where(
            MatterTodo.todo_id == todo_id,
            MatterTodo.matter_id == matter_id,
            MatterTodo.user_id == user.user_id,
        )
    )
    if todo is None:
        raise NotFoundError("todo_not_found", "待办不存在。")
    todo.status = "done" if done else "open"
    todo.completed_at = datetime.now(UTC) if done else None
    if done and cancel_reminders:
        now = datetime.now(UTC)
        await db.execute(
            update(Reminder)
            .where(
                Reminder.user_id == user.user_id,
                Reminder.todo_id == todo.todo_id,
                Reminder.status == "pending",
            )
            .values(status="cancelled", cancelled_at=now)
        )
    await db.commit()
    return _serialize_todo(todo)


def _normalise_title(value: str) -> set[str]:
    """Character bigrams: works for Chinese titles without a word segmenter."""

    text = "".join(
        char
        for char in value.lower()
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )
    if not text:
        return set()
    if len(text) <= 2:
        return {text}
    return {text[index : index + 2] for index in range(len(text) - 1)}


async def _match_active_matter(
    db: AsyncSession, user: User, title: str
) -> tuple[Matter | None, list[Matter]]:
    rows = list(
        (
            await db.scalars(
                select(Matter)
                .where(
                    Matter.user_id == user.user_id,
                    Matter.deleted_at.is_(None),
                    Matter.status.in_(("active", "waiting")),
                )
                .order_by(Matter.updated_at.desc())
                .limit(50)
            )
        ).all()
    )
    target_tokens = _normalise_title(title)
    if not target_tokens:
        return None, []
    scored = []
    for matter in rows:
        matter_tokens = _normalise_title(matter.title)
        if not matter_tokens:
            continue
        overlap = len(target_tokens & matter_tokens)
        score = overlap / max(1, len(target_tokens | matter_tokens))
        if score >= 0.6:
            scored.append((score, matter))
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) == 1:
        return scored[0][1], [scored[0][1]]
    if len(scored) > 1:
        return None, [matter for _, matter in scored]
    return None, []


async def _extraction_context(
    db: AsyncSession,
    user: User,
    *,
    text: str,
    timezone: str,
    pinned: Matter | None,
) -> tuple[dict, dict[str, list[dict]]]:
    query = select(Matter).where(
        Matter.user_id == user.user_id,
        Matter.deleted_at.is_(None),
        Matter.status.in_(("active", "waiting")),
    )
    if pinned is not None:
        query = query.where(Matter.matter_id == pinned.matter_id)
    matters = list((await db.scalars(query.order_by(Matter.updated_at.desc()).limit(30))).all())
    todos: dict[str, list[dict]] = {}
    if matters:
        rows = (
            await db.scalars(
                select(MatterTodo).where(
                    MatterTodo.matter_id.in_([matter.matter_id for matter in matters])
                )
            )
        ).all()
        for todo in rows:
            todos.setdefault(todo.matter_id, []).append(
                {"todo_id": todo.todo_id, "title": todo.title, "status": todo.status}
            )
    context = {
        "text": text,
        "now": datetime.now(UTC).isoformat(),
        "timezone": timezone,
        "pinned_matter_id": pinned.matter_id if pinned else None,
        "matters": [
            {
                "matter_id": matter.matter_id,
                "title": matter.title,
                "status": matter.status,
                "next_step": matter.next_step,
            }
            for matter in matters
        ],
        "matter_ids": [matter.matter_id for matter in matters],
        "todos": todos,
        "todo_ids": [item["todo_id"] for rows in todos.values() for item in rows],
    }
    return context, todos


async def intake(
    db: AsyncSession,
    client: NexusPilotClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    runtime: CommuteRuntime,
    *,
    user: User,
    text: str,
    idempotency_key: str,
    matter_id: str | None = None,
) -> tuple[dict | None, bool, str, str | None]:
    pinned = await _get_matter(db, user, matter_id) if matter_id else None
    if pinned is not None and pinned.status in ("archived", "deleted"):
        raise ConflictError("matter_not_active", "该事项已归档，不能继续补充进展。")

    run_key = f"matter-intake:{idempotency_key}"
    existing_run = await db.scalar(
        select(NexusRun).where(
            NexusRun.user_id == user.user_id,
            NexusRun.idempotency_key == run_key,
        )
    )
    if existing_run is not None and existing_run.purpose not in (None, matter_plan.PURPOSE):
        raise ConflictError(
            "idempotency_key_reused", "该幂等键已用于其他类型的请求。"
        )

    context: dict | None = None
    run: NexusRun | None = None
    replayed = False
    if existing_run is not None and existing_run.context_json:
        stored = dict(existing_run.context_json)
        if stored.get("text") != text or stored.get("pinned_matter_id") != (
            pinned.matter_id if pinned else None
        ):
            raise ConflictError(
                "idempotency_key_reused", "该幂等键已用于另一句话或另一事项。"
            )
        context = stored
        run = existing_run
        replayed = True

    if run is None:
        timezone = await user_timezone(db, user, settings.matter_default_timezone)
        context, _ = await _extraction_context(
            db, user, text=text, timezone=timezone, pinned=pinned
        )
        run, replayed = await run_service.create_ai_task(
            db,
            user=user,
            purpose=matter_plan.PURPOSE,
            trigger="chat",
            idempotency_key=run_key,
            context=context,
            business_ref=pinned.matter_id if pinned else None,
        )
        if run.context_json is None:
            run.context_json = context
            await db.commit()

    existing = await db.scalar(
        select(MatterDraft).where(
            MatterDraft.user_id == user.user_id,
            MatterDraft.ai_run_id == run.local_run_id,
        )
    )
    if existing is not None:
        return _serialize_draft(existing), True, "matter", None

    if run.status not in run_service.TERMINAL_STATUSES:
        if replayed:
            run = await run_service.resume_ai_task(
                db, client, settings, session_factory, user=user, row=run
            )
        else:
            run = await run_service.launch_ai_task(
                db, client, settings, session_factory, user=user, row=run
            )
        if run.status not in run_service.TERMINAL_STATUSES:
            run, timed_out = await run_service.wait_terminal(
                db,
                client,
                user=user,
                row=run,
                timeout_seconds=settings.matter_ai_timeout_seconds,
            )
            if timed_out and run.status not in run_service.TERMINAL_STATUSES:
                raise AssistantError(
                    "matter_ai_timeout",
                    "事项整理超时。可用同一 idempotency_key 重试，或用 GET /matters/drafts 找回已生成的预览。",
                    504,
                )
    if run.status == "unknown":
        raise AssistantError(
            "matter_ai_unknown", "事项整理结果未知，请用同一幂等键重试。", 504
        )
    if run.status != "succeeded":
        raise AssistantError("matter_ai_failed", "事项整理失败，请稍后重试。", 502)

    assert context is not None
    try:
        parsed = matter_plan.SPEC.validate_result(
            run_service.answer_text(run), context
        )
    except ai_tasks.AiTaskResultRejected as exc:
        raise InvalidRequestError(
            "matter_extraction_invalid", f"整理结果未通过校验：{exc.reason}"
        ) from None

    if parsed.get("intent") == "question":
        # One model run answers the question here; no draft and no second run.
        return None, False, "question", parsed.get("answer")
    if parsed.get("intent") == "unclear":
        raise InvalidRequestError(
            "matter_intent_unclear",
            parsed.get("clarify")
            or "这句话还不足以形成事项，请补充目标、时间或要办的事。",
        )

    timezone = context["timezone"]
    target: Matter | None = pinned
    candidates: list[dict] = []
    requires_choice = False
    if target is None and parsed["matter_id"]:
        target = await _get_matter(db, user, parsed["matter_id"])
    if target is None and parsed["candidate_matter_ids"]:
        matched = [
            await _get_matter(db, user, candidate)
            for candidate in parsed["candidate_matter_ids"][:5]
        ]
        candidates = await _serialize_matters(db, matched)
        requires_choice = True
        parsed["kind"] = "update"
    if target is None and parsed["kind"] == "create":
        matched, ambiguous = await _match_active_matter(db, user, parsed["title"] or "")
        if matched is not None:
            target = matched
            parsed["kind"] = "update"
        elif ambiguous:
            candidates = await _serialize_matters(db, ambiguous)
            requires_choice = True
            parsed["kind"] = "update"

    allowed_todos: set[str] = set()
    if target is not None:
        rows = (
            await db.scalars(
                select(MatterTodo).where(MatterTodo.matter_id == target.matter_id)
            )
        ).all()
        allowed_todos = {row.todo_id for row in rows}
    parsed["complete_todo_ids"] = [
        todo_id for todo_id in parsed["complete_todo_ids"] if todo_id in allowed_todos
    ]

    payload = {
        "kind": "update" if (target is not None or requires_choice) else "create",
        "matter_id": target.matter_id if target else None,
        "requires_choice": requires_choice,
        "title": parsed["title"] or (target.title if target else None),
        "goal": parsed["goal"] or (target.goal if target else ""),
        "changes_note": parsed["changes_note"],
        "next_step": parsed["next_step"] or (target.next_step if target else None),
        "schedule": parsed["schedule"],
        "todos": parsed["todos"],
        "complete_todo_ids": parsed["complete_todo_ids"],
        "source_text": text,
        "analysis": {
            "ai_run_id": run.local_run_id,
            "confidence": parsed["confidence"],
            "dropped_complete_todo_ids": parsed["dropped_complete_todo_ids"],
        },
    }

    base_schedule = None
    if target is not None and payload["schedule"] is not None:
        schedules = list(
            (
                await db.scalars(
                    select(ScheduleEvent).where(
                        ScheduleEvent.matter_id == target.matter_id,
                        ScheduleEvent.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        future = [
            row
            for row in schedules
            if ensure_aware(row.starts_at) >= datetime.now(UTC) - timedelta(hours=1)
        ]
        if len(future) == 1:
            base_schedule = future[0]
            payload["schedule"]["schedule_id"] = base_schedule.schedule_id

    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        payload,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(payload["schedule"] or {}).get("schedule_id"),
    )
    payload = _jsonable(payload)
    draft = MatterDraft(
        user_id=user.user_id,
        kind=payload["kind"],
        matter_id=target.matter_id if target else None,
        status="preview",
        version=1,
        payload_json=payload,
        conflict_json=_jsonable(conflict) if conflict else None,
        candidates_json=_jsonable(candidates) if candidates else None,
        base_matter_version=target.version if target else None,
        base_schedule_id=base_schedule.schedule_id if base_schedule else None,
        base_schedule_updated_at=base_schedule.updated_at if base_schedule else None,
        ai_run_id=run.local_run_id,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft), False, "matter", None


async def list_drafts(
    db: AsyncSession,
    user: User,
    *,
    status: str | None = None,
    matter_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    deleted_matters = select(Matter.matter_id).where(
        Matter.user_id == user.user_id, Matter.deleted_at.is_not(None)
    )
    query = select(MatterDraft).where(
        MatterDraft.user_id == user.user_id,
        or_(
            MatterDraft.matter_id.is_(None),
            MatterDraft.matter_id.notin_(deleted_matters),
        ),
    )
    if status is not None:
        query = query.where(MatterDraft.status == status)
    if matter_id is not None:
        query = query.where(MatterDraft.matter_id == matter_id)
    rows = (
        await db.scalars(query.order_by(MatterDraft.updated_at.desc()).limit(limit))
    ).all()
    return [_serialize_draft(row) for row in rows]


async def get_draft(
    db: AsyncSession,
    runtime: CommuteRuntime,
    settings: Settings,
    user: User,
    draft_id: str,
) -> dict:
    draft = await _get_draft(db, user, draft_id)
    if draft.matter_id:
        owner = await db.scalar(
            select(Matter).where(
                Matter.matter_id == draft.matter_id,
                Matter.user_id == user.user_id,
            )
        )
        if owner is None or owner.deleted_at is not None:
            raise NotFoundError("draft_not_found", "变更预览不存在。")
    timezone = await user_timezone(db, user, settings.matter_default_timezone)
    payload = draft.payload_json or {}
    if draft.status == "preview" and payload.get("schedule"):
        adjusted = None
        adjustment = payload.get("previous_adjustment")
        if adjustment and payload.get("selected_option") == "B":
            target = await db.scalar(
                select(ScheduleEvent).where(
                    ScheduleEvent.schedule_id == adjustment.get("schedule_id"),
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.deleted_at.is_(None),
                )
            )
            if target is not None:
                delta = timedelta(seconds=int(adjustment.get("shift_seconds") or 0))
                adjusted = {
                    target.schedule_id: (
                        ensure_aware(target.starts_at) + delta,
                        (ensure_aware(target.ends_at) + delta)
                        if target.ends_at
                        else None,
                    )
                }
        conflict = await _conflict_for_payload(
            db,
            runtime,
            user,
            payload,
            settings=settings,
            timezone=timezone,
            exclude_schedule_id=payload["schedule"].get("schedule_id"),
            adjusted=adjusted,
        )
        draft.conflict_json = _jsonable(conflict) if conflict else None
        await db.commit()
        await db.refresh(draft)
    return _serialize_draft(draft)


async def select_matter(
    db: AsyncSession,
    runtime: CommuteRuntime,
    settings: Settings,
    user: User,
    draft_id: str,
    payload: schemas.DraftSelectMatterRequest,
) -> dict:
    draft = await _get_draft(db, user, draft_id)
    if draft.status != "preview":
        raise ConflictError("draft_not_editable", "该预览已确认或已取消。")
    if draft.version != payload.expected_version:
        raise ConflictError("preview_stale", "预览已更新，请刷新后再选。")
    candidate_ids = {
        candidate["matter_id"] for candidate in (draft.candidates_json or [])
    }
    if payload.matter_id not in candidate_ids:
        raise InvalidRequestError(
            "matter_choice_required", "请选择预览中列出的候选事项。"
        )
    matter = await _get_matter(db, user, payload.matter_id)
    if matter.status not in ("active", "waiting"):
        raise ConflictError("matter_not_active", "该事项当前不可接续。")
    source = (
        draft.option_base_json
        if draft.option_base_json is not None
        else draft.payload_json or {}
    )
    body = deepcopy(source)
    body["matter_id"] = matter.matter_id
    body["requires_choice"] = False
    body["kind"] = "update"
    # A previously chosen adjustment was computed for the old candidate; it
    # must not survive this re-binding.
    body.pop("selected_option", None)
    body.pop("previous_adjustment", None)
    body.pop("affected", None)
    draft.option_base_json = None
    draft.base_matter_version = matter.version
    timezone = await user_timezone(db, user, settings.matter_default_timezone)
    schedule = body.get("schedule")
    if schedule and not schedule.get("schedule_id"):
        rows = list(
            (
                await db.scalars(
                    select(ScheduleEvent).where(
                        ScheduleEvent.matter_id == matter.matter_id,
                        ScheduleEvent.user_id == user.user_id,
                        ScheduleEvent.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        future = [
            row
            for row in rows
            if ensure_aware(row.starts_at) >= datetime.now(UTC) - timedelta(hours=1)
        ]
        if len(future) == 1:
            schedule["schedule_id"] = future[0].schedule_id
            draft.base_schedule_id = future[0].schedule_id
            draft.base_schedule_updated_at = future[0].updated_at
    draft.payload_json = _jsonable(body)
    draft.version += 1
    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        body,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(schedule or {}).get("schedule_id"),
    )
    draft.conflict_json = _jsonable(conflict) if conflict else None
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft)


async def apply_option(
    db: AsyncSession,
    runtime: CommuteRuntime,
    settings: Settings,
    user: User,
    draft_id: str,
    payload: schemas.DraftOptionRequest,
) -> dict:
    draft = await _get_draft(db, user, draft_id)
    if draft.status != "preview":
        raise ConflictError("draft_not_editable", "该预览已确认或已取消。")
    if draft.version != payload.expected_version:
        raise ConflictError("preview_stale", "预览已更新，请刷新后再选。")
    timezone = await user_timezone(db, user, settings.matter_default_timezone)
    if draft.base_schedule_id:
        base_schedule = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == draft.base_schedule_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
            )
        )
        if base_schedule is None or (
            draft.base_schedule_updated_at is not None
            and ensure_aware(base_schedule.updated_at)
            != ensure_aware(draft.base_schedule_updated_at)
        ):
            raise ConflictError(
                "schedule_changed", "关联日程已变化，请重新读取预览后再选。"
            )

    # Every selection is rebuilt from the same preview baseline. This prevents
    # B -> A from keeping B's neighbour shift, and makes A -> A idempotent.
    if draft.option_base_json is not None:
        base = _option_baseline(draft.option_base_json)
    else:
        base = _option_baseline(draft.payload_json or {})
        draft.option_base_json = _jsonable(base)
    body = deepcopy(base)

    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        base,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(base.get("schedule") or {}).get("schedule_id"),
    )
    options = {option["option_id"]: option for option in (conflict or {}).get("options", [])}
    option = options.get(payload.option_id)
    if option is None:
        raise InvalidRequestError(
            "option_not_available", "当前预览没有这个调整方案，请重新计算冲突。"
        )
    if option.get("executable") is False:
        raise InvalidRequestError(
            "option_not_executable",
            option.get("unavailable_reason") or "当前方案不能完整执行，请选择其他方案。",
        )

    adjusted: dict[str, tuple[datetime, datetime | None]] = {}
    if payload.option_id == "A":
        schedule = body.get("schedule")
        if not schedule:
            raise InvalidRequestError("option_not_applicable", "当前预览没有可调整的日程。")
        shift = timedelta(seconds=int(option.get("shift_seconds") or 0))
        start = _parse_dt(schedule["starts_at"], timezone)
        end = _parse_dt(schedule.get("ends_at"), timezone)
        schedule["starts_at"] = _jsonable(start + shift)
        schedule["ends_at"] = _jsonable(end + shift) if end else None
        body.pop("previous_adjustment", None)
        body["affected"] = []
    elif payload.option_id == "B":
        target_id = option.get("target_schedule_id")
        if not target_id:
            raise InvalidRequestError("option_not_applicable", "该方案没有可调整的相邻日程。")
        target = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == target_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
            )
        )
        if target is None:
            raise NotFoundError("schedule_not_found", "需要调整的日程不存在。")
        if not can_edit_schedule(target.source, target.data_source_id):
            raise InvalidRequestError(
                "option_not_executable",
                "需要调整的相邻日程来自只读副本，不能在应用内修改。",
            )
        delta = timedelta(seconds=int(option.get("shift_seconds") or 0))
        target_matter = (
            await db.scalar(
                select(Matter).where(
                    Matter.matter_id == target.matter_id,
                    Matter.user_id == user.user_id,
                )
            )
            if target.matter_id
            else None
        )
        body["previous_adjustment"] = {
            "schedule_id": target.schedule_id,
            "matter_id": target.matter_id,
            "matter_title": target_matter.title if target_matter else None,
            "title": target.title,
            "before_starts_at": _jsonable(target.starts_at),
            "before_ends_at": _jsonable(target.ends_at) if target.ends_at else None,
            "after_starts_at": _jsonable(ensure_aware(target.starts_at) + delta),
            "after_ends_at": (
                _jsonable(ensure_aware(target.ends_at) + delta)
                if target.ends_at
                else None
            ),
            "shift_seconds": int(option.get("shift_seconds") or 0),
            "base_updated_at": _jsonable(target.updated_at),
        }
        body["affected"] = [
            {
                "type": "schedule",
                "matter_id": target.matter_id,
                "matter_title": target_matter.title if target_matter else None,
                "schedule_id": target.schedule_id,
                "title": target.title,
                "source": target.source,
                "before_starts_at": _jsonable(target.starts_at),
                "before_ends_at": _jsonable(target.ends_at) if target.ends_at else None,
                "after_starts_at": _jsonable(ensure_aware(target.starts_at) + delta),
                "after_ends_at": (
                    _jsonable(ensure_aware(target.ends_at) + delta)
                    if target.ends_at
                    else None
                ),
            }
        ]
        adjusted[target.schedule_id] = (
            ensure_aware(target.starts_at) + delta,
            (ensure_aware(target.ends_at) + delta) if target.ends_at else None,
        )
        todo_title = option.get("creates_todo")
        if todo_title:
            titles = {item.get("title") for item in body.get("todos") or []}
            if todo_title not in titles:
                body.setdefault("todos", []).append(
                    {"todo_id": None, "title": todo_title, "due_at": None,
                     "reminder_at": None, "status": "open"}
                )
    else:
        body.pop("previous_adjustment", None)
        body["affected"] = []

    body["selected_option"] = payload.option_id
    draft.payload_json = _jsonable(body)
    draft.version += 1
    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        body,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(body.get("schedule") or {}).get("schedule_id"),
        adjusted=adjusted or None,
    )
    draft.conflict_json = _jsonable(conflict) if conflict else None
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft)


async def patch_draft(
    db: AsyncSession,
    runtime: CommuteRuntime,
    settings: Settings,
    user: User,
    draft_id: str,
    payload: schemas.DraftPatchRequest,
) -> dict:
    draft = await _get_draft(db, user, draft_id)
    if draft.status != "preview":
        raise ConflictError("draft_not_editable", "该预览已确认或已取消。")
    if draft.version != payload.expected_version:
        raise ConflictError("preview_stale", "预览已更新，请刷新后再改。")
    timezone = await user_timezone(db, user, settings.matter_default_timezone)
    source = (
        draft.option_base_json
        if draft.option_base_json is not None
        else draft.payload_json or {}
    )
    merged = deepcopy(source)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})

    for key in ("title", "goal", "changes_note", "next_step"):
        if key in changes:
            if key == "title" and changes[key] is None:
                continue
            merged[key] = changes[key]

    if "schedule" in changes:
        if changes["schedule"] is None:
            merged["schedule"] = None
        else:
            old = merged.get("schedule") or {}
            schedule = dict(changes["schedule"])
            if not schedule.get("schedule_id") and old.get("schedule_id"):
                schedule["schedule_id"] = old["schedule_id"]
            if schedule.get("ends_at") is not None:
                if _parse_dt(schedule["ends_at"], timezone) <= _parse_dt(
                    schedule["starts_at"], timezone
                ):
                    raise InvalidRequestError(
                        "schedule_time_order", "日程结束时间必须晚于开始时间。"
                    )
            schedule["starts_at"] = _parse_dt(schedule["starts_at"], timezone)
            schedule["ends_at"] = _parse_dt(schedule.get("ends_at"), timezone)
            old_location = old.get("location")
            new_location = schedule.get("location")
            if new_location != old_location or None in (
                schedule.get("latitude"),
                schedule.get("longitude"),
            ):
                schedule["latitude"] = None
                schedule["longitude"] = None
            merged["schedule"] = schedule

    if "todos" in changes and changes["todos"] is not None:
        matter_ids = {draft.payload_json.get("matter_id")} if draft.payload_json else set()
        allowed = set()
        if draft.matter_id:
            matter_ids.add(draft.matter_id)
        if any(matter_ids):
            rows = (
                await db.scalars(
                    select(MatterTodo.todo_id).where(
                        MatterTodo.matter_id.in_([m for m in matter_ids if m])
                    )
                )
            ).all()
            allowed = set(rows)
        todos = []
        for item in changes["todos"]:
            todo = dict(item)
            todo["due_at"] = _parse_dt(todo.get("due_at"), timezone)
            todo["reminder_at"] = _parse_dt(todo.get("reminder_at"), timezone)
            if todo.get("todo_id") and todo["todo_id"] not in allowed:
                raise InvalidRequestError(
                    "todo_not_in_matter", "待办不属于该事项。"
                )
            todos.append(todo)
        merged["todos"] = todos

    if "complete_todo_ids" in changes and changes["complete_todo_ids"] is not None:
        allowed = set()
        target_id = (draft.payload_json or {}).get("matter_id") or draft.matter_id
        if target_id:
            allowed = set(
                (
                    await db.scalars(
                        select(MatterTodo.todo_id).where(
                            MatterTodo.matter_id == target_id
                        )
                    )
                ).all()
            )
        unknown = [todo_id for todo_id in changes["complete_todo_ids"] if todo_id not in allowed]
        if unknown:
            raise InvalidRequestError("todo_not_in_matter", "待办不属于该事项。")
        merged["complete_todo_ids"] = changes["complete_todo_ids"]

    merged.pop("previous_adjustment", None)
    merged.pop("selected_option", None)
    merged.pop("affected", None)
    # A manual edit invalidates both the option baseline and the selection.
    draft.option_base_json = None
    schedule = merged.get("schedule")
    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        merged,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(schedule or {}).get("schedule_id"),
    )
    draft.payload_json = _jsonable(merged)
    draft.version += 1
    draft.conflict_json = _jsonable(conflict) if conflict else None
    await db.commit()
    await db.refresh(draft)
    return _serialize_draft(draft)


async def cancel_draft(db: AsyncSession, user: User, draft_id: str) -> dict:
    draft = await _get_draft(db, user, draft_id)
    if draft.status == "confirmed":
        raise ConflictError("draft_confirmed", "该预览已确认，不能取消；可撤回变更。")
    if draft.status == "preview":
        draft.status = "cancelled"
        draft.cancelled_at = datetime.now(UTC)
        await db.commit()
    return _serialize_draft(draft)


# ---------------------------------------------------------------------------
# confirmation
# ---------------------------------------------------------------------------


async def confirm_draft(
    db: AsyncSession,
    runtime: CommuteRuntime,
    settings: Settings,
    user: User,
    draft_id: str,
    body: schemas.ConfirmRequest,
    idempotency_key: str,
) -> dict:
    draft = await _get_draft(db, user, draft_id)
    if draft.status == "confirmed":
        if draft.confirm_key == idempotency_key:
            return await _confirm_response(db, user, draft, replayed=True)
        raise ConflictError("draft_already_confirmed", "该预览已确认。")
    if draft.status != "preview":
        raise ConflictError("draft_cancelled", "该预览已取消，请重新整理。")
    if draft.version != body.expected_version:
        raise ConflictError("preview_stale", "预览已更新，请刷新后确认。")

    other_key = await db.scalar(
        select(MatterDraft).where(
            MatterDraft.user_id == user.user_id,
            MatterDraft.confirm_key == idempotency_key,
            MatterDraft.draft_id != draft.draft_id,
        )
    )
    if other_key is not None:
        raise ConflictError(
            "idempotency_key_reused", "该确认请求标识已用于另一次确认。"
        )

    payload = dict(draft.payload_json or {})
    if body.option_id is not None and payload.get("selected_option") != body.option_id:
        raise ConflictError(
            "option_not_selected", "请先在预览中选择调整方案，再提交确认。"
        )
    timezone = await user_timezone(db, user, settings.matter_default_timezone)

    claim = await db.execute(
        update(MatterDraft)
        .where(
            MatterDraft.draft_id == draft.draft_id,
            MatterDraft.user_id == user.user_id,
            MatterDraft.status == "preview",
            MatterDraft.version == body.expected_version,
        )
        .values(status="confirmed", confirm_key=idempotency_key)
    )
    if claim.rowcount == 0:
        await db.rollback()
        fresh = await _get_draft(db, user, draft_id)
        if fresh.status == "confirmed" and fresh.confirm_key == idempotency_key:
            return await _confirm_response(db, user, fresh, replayed=True)
        raise ConflictError("preview_stale", "预览已更新，请刷新后确认。")

    try:
        if payload.get("requires_choice"):
            candidate_ids = {
                candidate["matter_id"] for candidate in (draft.candidates_json or [])
            }
            if not body.matter_id or body.matter_id not in candidate_ids:
                raise InvalidRequestError(
                    "matter_choice_required", "存在多个可能事项，请先选择要接续的事项。"
                )
            payload["matter_id"] = body.matter_id
            payload["requires_choice"] = False
            payload["kind"] = "update"

        matter: Matter | None = None
        matter_id = payload.get("matter_id")
        base_matter_version = draft.base_matter_version
        if payload.get("kind") == "update":
            if not matter_id:
                raise InvalidRequestError("matter_required", "接续更新缺少事项。")
            matter = await _get_matter(db, user, matter_id)
            if base_matter_version is None:
                # Ambiguous draft: pin the version at the moment the user chooses.
                base_matter_version = matter.version
            elif matter.version != base_matter_version:
                raise ConflictError("matter_changed", "该事项已被修改，请重新整理。")
            if matter.status not in ("active", "waiting"):
                raise ConflictError("matter_not_active", "该事项当前不可接续。")

        existing_todos: dict[str, MatterTodo] = {}
        if matter is not None:
            rows = (
                await db.scalars(
                    select(MatterTodo).where(MatterTodo.matter_id == matter.matter_id)
                )
            ).all()
            existing_todos = {row.todo_id: row for row in rows}

        if draft.base_todo_id:
            base_todo = existing_todos.get(draft.base_todo_id)
            if base_todo is None:
                raise NotFoundError("todo_not_found", "预览对应的待办不存在。")
            if draft.base_todo_updated_at is not None and ensure_aware(
                base_todo.updated_at
            ) != ensure_aware(draft.base_todo_updated_at):
                raise ConflictError(
                    "todo_changed", "该待办已被其他操作修改，请刷新后再确认。"
                )

        proposed = payload.get("schedule")
        schedule_row: ScheduleEvent | None = None
        if proposed and proposed.get("schedule_id"):
            schedule_row = await db.scalar(
                select(ScheduleEvent).where(
                    ScheduleEvent.schedule_id == proposed["schedule_id"],
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.deleted_at.is_(None),
                )
            )
            if schedule_row is None:
                raise NotFoundError("schedule_not_found", "关联日程不存在。")
            if proposed.get("schedule_id") and schedule_row.matter_id != matter_id:
                raise ConflictError(
                    "schedule_not_in_matter", "该日程属于另一个事项，不能挂到当前事项。"
                )
            if not can_edit_schedule(schedule_row.source, schedule_row.data_source_id):
                raise InvalidRequestError(
                    "schedule_not_editable", "设备导入的日程不能在应用内改写。"
                )
            if (
                draft.base_schedule_updated_at is not None
                and ensure_aware(schedule_row.updated_at)
                != ensure_aware(draft.base_schedule_updated_at)
            ):
                raise ConflictError("schedule_changed", "关联日程已变化，请重新整理。")

        for item in payload.get("todos") or []:
            if item.get("todo_id") and item["todo_id"] not in existing_todos:
                raise InvalidRequestError("todo_not_in_matter", "待办不属于该事项。")
        for todo_id in payload.get("complete_todo_ids") or []:
            if todo_id not in existing_todos:
                raise InvalidRequestError("todo_not_in_matter", "待办不属于该事项。")

        adjustment = payload.get("previous_adjustment")
        if adjustment and payload.get("selected_option") != "B":
            raise ConflictError(
                "option_not_selected", "相邻日程调整没有对应已选方案，请重新选择。"
            )
        adjacent_before = None
        adjacent_after = None
        if adjustment:
            target = await db.scalar(
                select(ScheduleEvent).where(
                    ScheduleEvent.schedule_id == adjustment["schedule_id"],
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.deleted_at.is_(None),
                )
            )
            if target is None:
                raise NotFoundError("schedule_not_found", "需要调整的相邻日程不存在。")
            if not can_edit_schedule(target.source, target.data_source_id):
                raise InvalidRequestError(
                    "option_not_executable",
                    "需要调整的相邻日程来自只读副本，不能在应用内修改。",
                )
            base = _parse_dt(adjustment["base_updated_at"], timezone)
            if ensure_aware(target.updated_at) != base:
                raise ConflictError("schedule_changed", "相邻日程已变化，请重新选择方案。")
            adjacent_before = _schedule_snapshot(target)
            delta = timedelta(seconds=int(adjustment["shift_seconds"]))
            result = await db.execute(
                update(ScheduleEvent)
                .where(
                    ScheduleEvent.schedule_id == target.schedule_id,
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.updated_at == target.updated_at,
                )
                .values(
                    starts_at=ensure_aware(target.starts_at) + delta,
                    ends_at=(ensure_aware(target.ends_at) + delta)
                    if target.ends_at
                    else None,
                    sync_status="local_only",
                )
            )
            if result.rowcount == 0:
                raise ConflictError("schedule_changed", "相邻日程已变化，请重新选择方案。")
            await db.refresh(target)
            adjacent_after = _schedule_snapshot(target)

        now = datetime.now(UTC)
        before = {
            "matter": None,
            "schedule": None,
            "todos": [],
            "reminders": [],
            "adjacent_schedules": [adjacent_before] if adjacent_before else [],
        }
        before_todos: dict[str, dict] = {}
        before_reminders: dict[str, dict] = {}
        affected_todos: dict[str, MatterTodo] = {}
        affected_reminders: dict[str, Reminder] = {}
        created_todo_ids: list[str] = []
        created_reminder_ids: list[str] = []
        if payload["kind"] == "create":
            matter = Matter(
                user_id=user.user_id,
                title=(payload.get("title") or "未命名事项").strip(),
                goal=payload.get("goal") or "",
                next_step=payload.get("next_step"),
                status="active",
                last_progress_at=now,
            )
            db.add(matter)
            await db.flush()
        else:
            assert matter is not None
            before["matter"] = _matter_fields(matter)
            values: dict = {"version": matter.version + 1, "last_progress_at": now}
            if payload.get("title"):
                values["title"] = payload["title"].strip()
            if payload.get("goal") is not None:
                values["goal"] = payload["goal"]
            if payload.get("next_step"):
                values["next_step"] = payload["next_step"]
            result = await db.execute(
                update(Matter)
                .where(
                    Matter.matter_id == matter.matter_id,
                    Matter.user_id == user.user_id,
                    Matter.version == base_matter_version,
                )
                .values(**values)
            )
            if result.rowcount == 0:
                raise ConflictError("matter_changed", "该事项已被修改，请重新整理。")
            await db.refresh(matter)

        if body.create_schedule and proposed:
            before["schedule"] = _schedule_snapshot(schedule_row)
            sync_status = "pending_ios" if body.sync_to_ios else "local_only"
            values = {
                "title": proposed["title"],
                "starts_at": _parse_dt(proposed["starts_at"], timezone),
                "ends_at": _parse_dt(proposed.get("ends_at"), timezone),
                "location": proposed.get("location"),
                "latitude": proposed.get("latitude"),
                "longitude": proposed.get("longitude"),
                "sync_status": sync_status,
                "source": "web_native",
                "matter_id": matter.matter_id,
            }
            if schedule_row is None:
                schedule_row = ScheduleEvent(
                    user_id=user.user_id,
                    status="scheduled",
                    **values,
                )
                db.add(schedule_row)
                await db.flush()
            else:
                result = await db.execute(
                    update(ScheduleEvent)
                    .where(
                        ScheduleEvent.schedule_id == schedule_row.schedule_id,
                        ScheduleEvent.user_id == user.user_id,
                        ScheduleEvent.updated_at == schedule_row.updated_at,
                    )
                    .values(**values)
                )
                if result.rowcount == 0:
                    raise ConflictError("schedule_changed", "关联日程已变化，请重新整理。")
                await db.refresh(schedule_row)

        position = len(existing_todos)
        for item in payload.get("todos") or []:
            todo_id = item.get("todo_id")
            if todo_id and todo_id in existing_todos:
                todo = existing_todos[todo_id]
                before_todos.setdefault(todo.todo_id, _todo_snapshot(todo))
                todo.title = item["title"]
                todo.due_at = _parse_dt(item.get("due_at"), timezone)
                todo.reminder_at = _parse_dt(item.get("reminder_at"), timezone)
            else:
                todo = MatterTodo(
                    matter_id=matter.matter_id,
                    user_id=user.user_id,
                    title=item["title"],
                    due_at=_parse_dt(item.get("due_at"), timezone),
                    reminder_at=_parse_dt(item.get("reminder_at"), timezone),
                    position=position,
                )
                position += 1
                db.add(todo)
                await db.flush()
                existing_todos[todo.todo_id] = todo
                created_todo_ids.append(todo.todo_id)
            affected_todos[todo.todo_id] = todo
            pending = await db.scalar(
                select(Reminder).where(
                    Reminder.user_id == user.user_id,
                    Reminder.todo_id == todo.todo_id,
                    Reminder.status == "pending",
                )
            )
            if pending is not None:
                before_reminders.setdefault(
                    pending.reminder_id, _reminder_snapshot(pending)
                )
                affected_reminders[pending.reminder_id] = pending
            if todo.reminder_at is None:
                if pending is not None:
                    pending.status = "cancelled"
                    pending.cancelled_at = now
            elif pending is None:
                reminder = Reminder(
                    user_id=user.user_id,
                    matter_id=matter.matter_id,
                    todo_id=todo.todo_id,
                    title=f"提醒：{todo.title}",
                    trigger_at=todo.reminder_at,
                    status="pending",
                    sync_status="pending_ios" if body.sync_to_ios else "local_only",
                    source="web_native",
                )
                db.add(reminder)
                await db.flush()
                affected_reminders[reminder.reminder_id] = reminder
                created_reminder_ids.append(reminder.reminder_id)
            else:
                pending.trigger_at = todo.reminder_at
                pending.title = f"提醒：{todo.title}"

        for todo_id in payload.get("complete_todo_ids") or []:
            todo = existing_todos.get(todo_id)
            if todo is not None:
                if todo.todo_id not in before_todos:
                    before_todos[todo.todo_id] = _todo_snapshot(todo)
                affected_todos[todo.todo_id] = todo
                todo.status = "done"
                todo.completed_at = now

        await db.flush()
        all_after_todos = {
            todo_id: _todo_snapshot(todo)
            for todo_id, todo in affected_todos.items()
        }
        all_after_reminders = {
            reminder_id: _reminder_snapshot(reminder)
            for reminder_id, reminder in affected_reminders.items()
        }
        after_todos = {
            todo_id: snapshot
            for todo_id, snapshot in all_after_todos.items()
            if before_todos.get(todo_id) is None or snapshot != before_todos.get(todo_id)
        }
        after_reminders = {
            reminder_id: snapshot
            for reminder_id, snapshot in all_after_reminders.items()
            if before_reminders.get(reminder_id) is None
            or snapshot != before_reminders.get(reminder_id)
        }
        before["todos"] = [
            before_todos[todo_id]
            for todo_id in after_todos
            if todo_id in before_todos
        ]
        before["reminders"] = [
            before_reminders[reminder_id]
            for reminder_id in after_reminders
            if reminder_id in before_reminders
        ]

        conflict = await _conflict_for_payload(
            db,
            runtime,
            user,
            payload,
            settings=settings,
            timezone=timezone,
            exclude_schedule_id=(
                schedule_row.schedule_id
                if body.create_schedule and schedule_row is not None
                else (proposed or {}).get("schedule_id")
            ),
        )
        if conflict and conflict.get("has_conflict") and not body.acknowledge_conflict:
            raise ConflictError(
                "conflict_detected",
                "本次变更与已有日程冲突，请先选择调整方案或明确接受风险。",
            )

        db.add(
            MatterProgress(
                matter_id=matter.matter_id,
                user_id=user.user_id,
                text=payload.get("changes_note") or payload.get("source_text") or "更新事项",
                kind="created" if payload["kind"] == "create" else "progress",
                source_run_id=(payload.get("analysis") or {}).get("ai_run_id"),
            )
        )
        change = MatterChange(
            matter_id=matter.matter_id,
            user_id=user.user_id,
            draft_id=draft.draft_id,
            change_type=payload["kind"],
            before_json=_jsonable(before),
            after_json=_jsonable(
                {
                    "matter": _matter_fields(matter),
                    "matter_version": matter.version,
                    "schedule": (
                        _schedule_snapshot(schedule_row)
                        if body.create_schedule and proposed
                        else None
                    ),
                    "todos": list(after_todos.values()),
                    "reminders": list(after_reminders.values()),
                    "adjacent_schedules": [adjacent_after] if adjacent_after else [],
                    "created_todo_ids": created_todo_ids,
                    "created_reminder_ids": created_reminder_ids,
                    "reminder_ids": created_reminder_ids,
                    "completed_todo_ids": payload.get("complete_todo_ids") or [],
                }
            ),
        )
        db.add(change)
        await db.flush()
        draft.confirm_key = idempotency_key
        draft.result_json = {
            "matter_id": matter.matter_id,
            "schedule_id": schedule_row.schedule_id
            if (body.create_schedule and schedule_row)
            else None,
            "reminder_ids": created_reminder_ids,
            "change_id": change.change_id,
            "conflict": _jsonable(conflict) if conflict else None,
        }
        await db.commit()
    except IntegrityError:
        await db.rollback()
        other_key = await db.scalar(
            select(MatterDraft).where(
                MatterDraft.user_id == user.user_id,
                MatterDraft.confirm_key == idempotency_key,
                MatterDraft.draft_id != draft.draft_id,
            )
        )
        if other_key is not None:
            raise ConflictError(
                "idempotency_key_reused", "该确认请求标识已用于另一次确认。"
            ) from None
        raise
    except AssistantError:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    await db.refresh(matter)
    await db.refresh(draft)
    return await _confirm_response(db, user, draft, replayed=False)


async def _confirm_response(
    db: AsyncSession, user: User, draft: MatterDraft, *, replayed: bool
) -> dict:
    result = draft.result_json or {}
    matter = await _get_matter(db, user, result["matter_id"])
    schedule = (
        await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == result.get("schedule_id"),
                ScheduleEvent.user_id == user.user_id,
            )
        )
        if result.get("schedule_id")
        else None
    )
    reminders = list(
        (
            await db.scalars(
                select(Reminder).where(
                    Reminder.reminder_id.in_(result.get("reminder_ids") or []),
                    Reminder.user_id == user.user_id,
                )
            )
        ).all()
    )
    matter_payload = (await _serialize_matters(db, [matter]))[0]
    change = (
        await db.scalar(
            select(MatterChange).where(
                MatterChange.change_id == result.get("change_id"),
                MatterChange.user_id == user.user_id,
            )
        )
        if result.get("change_id")
        else None
    )
    return {
        "draft": _serialize_draft(draft),
        "matter": matter_payload,
        "schedule": _serialize_schedule(schedule) if schedule else None,
        "reminders": [_serialize_reminder(reminder) for reminder in reminders],
        "change_id": result.get("change_id"),
        "replayed": replayed,
        "applied": True,
        "conflict": result.get("conflict"),
        "change": (
            {
                "change_id": change.change_id,
                "change_type": change.change_type,
                "before": change.before_json,
                "after": change.after_json,
            }
            if change is not None
            else None
        ),
    }


async def revert_change(
    db: AsyncSession,
    user: User,
    matter_id: str,
    change_id: str,
    *,
    expected_version: int,
) -> dict:
    matter = await _get_matter(db, user, matter_id)
    if matter.version != expected_version:
        raise ConflictError("matter_changed", "事项已更新，撤回前请刷新。")
    change = await db.scalar(
        select(MatterChange).where(
            MatterChange.change_id == change_id,
            MatterChange.user_id == user.user_id,
        )
    )
    if change is None:
        raise NotFoundError("change_not_found", "变更记录不存在。")
    if change.matter_id != matter.matter_id:
        after = change.after_json or {}
        current_schedule_ids = set(
            (
                await db.scalars(
                    select(ScheduleEvent.schedule_id).where(
                        ScheduleEvent.user_id == user.user_id,
                        ScheduleEvent.matter_id == matter.matter_id,
                    )
                )
            ).all()
        )
        linked_to_current = matter.matter_id in _affected_matter_ids(after) or any(
            isinstance(item, dict)
            and item.get("schedule_id") in current_schedule_ids
            for key in ("adjacent_schedules", "affected")
            for item in after.get(key) or []
        )
        if linked_to_current:
            source_matter = await db.scalar(
                select(Matter).where(
                    Matter.matter_id == change.matter_id,
                    Matter.user_id == user.user_id,
                )
            )
            source_label = (
                f"事项「{source_matter.title}」" if source_matter else "来源事项"
            )
            raise ConflictError(
                "revert_from_source_matter",
                f"该变更由{source_label}发起，请到来源事项处理撤回。",
            )
        raise NotFoundError("change_not_found", "变更记录不存在。")
    if change.reverted_at is not None:
        raise ConflictError("already_reverted", "该变更已撤回。")
    later = await db.scalar(
        select(MatterChange)
        .where(
            MatterChange.matter_id == matter.matter_id,
            MatterChange.created_at > change.created_at,
            MatterChange.change_id != change.change_id,
        )
        .limit(1)
    )
    if later is not None:
        raise ConflictError(
            "later_changes", "该变更之后还有新的修改，不能直接撤回覆盖。"
        )
    eligible, reason_code, reason_text = await _change_eligibility(
        db, user, matter, change
    )
    if not eligible:
        raise ConflictError(reason_code or "not_revertable", reason_text or "该变更不能撤回。")
    before = change.before_json or {}
    after = change.after_json or {}
    now = datetime.now(UTC)

    async def current_schedule(snapshot: dict) -> ScheduleEvent:
        current = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == snapshot.get("schedule_id"),
                ScheduleEvent.user_id == user.user_id,
            )
        )
        if not _schedule_matches(current, snapshot):
            raise ConflictError("schedule_changed", "关联日程已被修改，不能覆盖撤回。")
        return current

    async def restore_schedule(current: ScheduleEvent, snapshot: dict) -> None:
        result = await db.execute(
            update(ScheduleEvent)
            .where(
                ScheduleEvent.schedule_id == current.schedule_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.updated_at == current.updated_at,
            )
            .values(
                matter_id=snapshot.get("matter_id"),
                title=snapshot["title"],
                starts_at=_parse_dt(snapshot["starts_at"]),
                ends_at=_parse_dt(snapshot.get("ends_at")),
                location=snapshot.get("location"),
                status=snapshot.get("status") or "scheduled",
                source_run_id=snapshot.get("source_run_id"),
                latitude=snapshot.get("latitude"),
                longitude=snapshot.get("longitude"),
                source=snapshot.get("source"),
                sync_status=snapshot.get("sync_status") or "local_only",
                source_external_id=snapshot.get("source_external_id"),
                source_scope_key=snapshot.get("source_scope_key"),
                source_updated_at=_parse_dt(snapshot.get("source_updated_at")),
                source_version=snapshot.get("source_version"),
                data_source_id=snapshot.get("data_source_id"),
                deleted_at=None,
            )
        )
        if result.rowcount == 0:
            raise ConflictError("schedule_changed", "关联日程已被修改，不能覆盖撤回。")

    after_schedule = after.get("schedule")
    before_schedule = before.get("schedule")
    if after_schedule:
        current = await current_schedule(after_schedule)
        if before_schedule is None:
            result = await db.execute(
                update(ScheduleEvent)
                .where(
                    ScheduleEvent.schedule_id == current.schedule_id,
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.updated_at == current.updated_at,
                )
                .values(status="cancelled", deleted_at=now)
            )
            if result.rowcount == 0:
                raise ConflictError("schedule_changed", "关联日程已被修改，不能覆盖撤回。")
        else:
            await restore_schedule(current, before_schedule)

    before_adjacent = {
        item.get("schedule_id"): item
        for item in before.get("adjacent_schedules") or []
    }
    for adjacent_after in after.get("adjacent_schedules") or []:
        current = await current_schedule(adjacent_after)
        adjacent_before = before_adjacent.get(adjacent_after.get("schedule_id"))
        if adjacent_before is None:
            raise ConflictError(
                "schedule_changed", "缺少相邻日程的撤回依据，不能覆盖撤回。"
            )
        await restore_schedule(current, adjacent_before)

    after_reminders = {
        item.get("reminder_id"): item for item in after.get("reminders") or []
    }
    before_reminders = {
        item.get("reminder_id"): item for item in before.get("reminders") or []
    }
    if after_reminders:
        for reminder_id, reminder_after in after_reminders.items():
            current = await db.scalar(
                select(Reminder).where(
                    Reminder.reminder_id == reminder_id,
                    Reminder.user_id == user.user_id,
                )
            )
            if not _reminder_matches(current, reminder_after):
                raise ConflictError(
                    "reminder_changed", "提醒已被修改，不能覆盖撤回。"
                )
            reminder_before = before_reminders.get(reminder_id)
            if reminder_before is None:
                await db.delete(current)
            else:
                current.title = reminder_before["title"]
                current.note = reminder_before.get("note")
                current.trigger_at = _parse_dt(reminder_before["trigger_at"])
                current.status = reminder_before["status"]
                current.sync_status = reminder_before.get("sync_status")
                current.source = reminder_before.get("source")
                current.source_external_id = reminder_before.get("source_external_id")
                current.source_scope_key = reminder_before.get("source_scope_key")
                current.data_source_id = reminder_before.get("data_source_id")
                current.source_updated_at = _parse_dt(
                    reminder_before.get("source_updated_at")
                )
                current.source_version = reminder_before.get("source_version")
                current.completed_at = _parse_dt(reminder_before.get("completed_at"))
                current.cancelled_at = _parse_dt(reminder_before.get("cancelled_at"))
                current.result_json = reminder_before.get("result_json")
    after_todos = {item.get("todo_id"): item for item in after.get("todos") or []}
    before_todos = {item.get("todo_id"): item for item in before.get("todos") or []}
    if after_todos:
        for todo_id, todo_after in after_todos.items():
            current = await db.scalar(
                select(MatterTodo).where(
                    MatterTodo.todo_id == todo_id,
                    MatterTodo.matter_id == matter.matter_id,
                    MatterTodo.user_id == user.user_id,
                )
            )
            if not _todo_matches(current, todo_after):
                raise ConflictError("todo_changed", "待办已被修改，不能覆盖撤回。")
            todo_before = before_todos.get(todo_id)
            if todo_before is None:
                await db.delete(current)
            else:
                current.title = todo_before["title"]
                current.status = todo_before["status"]
                current.due_at = _parse_dt(todo_before.get("due_at"))
                current.reminder_at = _parse_dt(todo_before.get("reminder_at"))
                current.completed_at = _parse_dt(todo_before.get("completed_at"))
                if todo_before.get("position") is not None:
                    current.position = todo_before["position"]
    values = {"version": matter.version + 1}
    if before.get("matter"):
        values.update(before["matter"])
    result = await db.execute(
        update(Matter)
        .where(
            Matter.matter_id == matter.matter_id,
            Matter.user_id == user.user_id,
            Matter.version == expected_version,
        )
        .values(**values)
    )
    if result.rowcount == 0:
        raise ConflictError("matter_changed", "事项已更新，撤回前请刷新。")
    change.reverted_at = now
    db.add(
        MatterProgress(
            matter_id=matter.matter_id,
            user_id=user.user_id,
            text="已撤回上一次确认的变更。",
            kind="revert",
        )
    )
    await db.commit()
    await db.refresh(matter)
    return {
        "matter": (await _serialize_matters(db, [matter]))[0],
        "change_id": change.change_id,
        "reverted_at": now,
    }


async def _change_eligibility(
    db: AsyncSession, user: User, matter: Matter, change: MatterChange
) -> tuple[bool, str | None, str | None]:
    """Single source of truth for "can this change still be reverted?"."""

    if change.reverted_at is not None:
        return False, "already_reverted", "该变更已经撤回。"
    later = await db.scalar(
        select(MatterChange)
        .where(
            MatterChange.matter_id == matter.matter_id,
            MatterChange.created_at > change.created_at,
            MatterChange.change_id != change.change_id,
        )
        .limit(1)
    )
    if later is not None:
        return False, "later_changes", "该变更之后还有新的修改，不能直接撤回。"
    after = change.after_json or {}
    after_version = after.get("matter_version")
    if after_version is not None and matter.version != after_version:
        return False, "matter_changed", "事项在此之后被直接修改过。"
    if change.change_type == "create":
        return (
            False,
            "creation_revert_unsupported",
            "新建事项的完整撤回尚未定义（规划未决定是否删除新事项）；当前不能声称已撤销。",
        )
    before = change.before_json or {}
    if _change_snapshot_incomplete(before, after):
        return (
            False,
            "incomplete_change_snapshot",
            "这条旧变更缺少完整的前后快照，服务端无法保证全部恢复，因此不能撤回。",
        )

    schedule = after.get("schedule")
    if schedule:
        current = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == schedule.get("schedule_id"),
                ScheduleEvent.user_id == user.user_id,
            )
        )
        if not _schedule_matches(current, schedule):
            return False, "schedule_changed", "关联日程在此之后被修改或删除了。"

    for adjacent in after.get("adjacent_schedules") or []:
        current = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == adjacent.get("schedule_id"),
                ScheduleEvent.user_id == user.user_id,
            )
        )
        if not _schedule_matches(current, adjacent):
            return False, "schedule_changed", "相邻日程在此之后被修改或删除了。"

    for todo_snapshot in after.get("todos") or []:
        current = await db.scalar(
            select(MatterTodo).where(
                MatterTodo.todo_id == todo_snapshot.get("todo_id"),
                MatterTodo.matter_id == matter.matter_id,
                MatterTodo.user_id == user.user_id,
            )
        )
        if not _todo_matches(current, todo_snapshot):
            return False, "todo_changed", "待办在此之后被修改或删除了。"

    for reminder_snapshot in after.get("reminders") or []:
        current = await db.scalar(
            select(Reminder).where(
                Reminder.reminder_id == reminder_snapshot.get("reminder_id"),
                Reminder.user_id == user.user_id,
            )
        )
        if not _reminder_matches(current, reminder_snapshot):
            return False, "reminder_changed", "提醒在此之后被修改或删除了。"

    return True, None, None


async def list_changes(
    db: AsyncSession, user: User, matter_id: str
) -> list[dict]:
    matter = await _get_matter(db, user, matter_id)
    current_schedule_ids = set(
        (
            await db.scalars(
                select(ScheduleEvent.schedule_id).where(
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.matter_id == matter.matter_id,
                )
            )
        ).all()
    )

    def related_exists(path: str, alias_name: str):
        values = (
            func.json_each(MatterChange.after_json, path)
            .table_valued("value")
            .alias(alias_name)
        )
        return (
            select(1)
            .select_from(values)
            .where(
                or_(
                    func.json_extract(values.c.value, "$.matter_id")
                    == matter.matter_id,
                    func.json_extract(values.c.value, "$.schedule_id").in_(
                        current_schedule_ids
                    ),
                )
            )
            .exists()
        )

    related_by_schedule = or_(
        related_exists("$.adjacent_schedules", "affected_schedule"),
        related_exists("$.affected", "affected_record"),
    )
    rows = list(
        (
            await db.scalars(
                select(MatterChange)
                .where(
                    MatterChange.user_id == user.user_id,
                    or_(
                        MatterChange.matter_id == matter.matter_id,
                        related_by_schedule,
                    ),
                )
                .order_by(MatterChange.created_at.desc())
                .limit(50)
            )
        ).all()
    )
    source_matter_ids = {row.matter_id for row in rows if row.matter_id}
    source_titles = {
        row.matter_id: row.title
        for row in (
            await db.scalars(
                select(Matter).where(
                    Matter.user_id == user.user_id,
                    Matter.matter_id.in_(source_matter_ids),
                )
            )
        ).all()
    }
    items = []
    for change in rows:
        after = change.after_json or {}
        affected_matter_ids = _affected_matter_ids(after)
        is_primary = change.matter_id == matter.matter_id
        if is_primary:
            eligible, code, reason = await _change_eligibility(db, user, matter, change)
            relation = "primary"
            schedule_id = (after.get("schedule") or {}).get("schedule_id")
        else:
            eligible = False
            code = "revert_from_source_matter"
            reason = (
                f"该变更由事项「{source_titles.get(change.matter_id) or '来源事项'}」"
                "确认，请到来源事项处理撤回。"
            )
            relation = "affected"
            linked = next(
                (
                    item
                    for key in ("adjacent_schedules", "affected")
                    for item in after.get(key) or []
                    if isinstance(item, dict)
                    and (
                        item.get("matter_id") == matter.matter_id
                        or item.get("schedule_id") in current_schedule_ids
                    )
                ),
                None,
            )
            schedule_id = linked.get("schedule_id") if linked else None
        items.append(
            {
                "change_id": change.change_id,
                "matter_id": change.matter_id,
                "source_matter_id": change.matter_id,
                "source_matter_title": source_titles.get(change.matter_id),
                "relation": relation,
                "affected_matter_ids": affected_matter_ids,
                "change_type": change.change_type,
                "draft_id": change.draft_id,
                "created_at": change.created_at,
                "schedule_id": schedule_id,
                "before": change.before_json or {},
                "after": after,
                "revertable": eligible,
                "reason_code": code,
                "reason": reason,
            }
        )
    return items


async def create_edit_draft(
    db: AsyncSession,
    runtime: CommuteRuntime,
    settings: Settings,
    user: User,
    matter_id: str,
    payload: schemas.MatterEditRequest,
) -> dict:
    """Open one existing schedule/todo as an editable preview (no model call)."""

    matter = await _get_matter(db, user, matter_id)
    if matter.status not in ("active", "waiting"):
        raise ConflictError("matter_not_active", "该事项当前不可编辑。")
    existing = await db.scalar(
        select(MatterDraft).where(
            MatterDraft.user_id == user.user_id,
            MatterDraft.origin_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        if existing.matter_id != matter.matter_id:
            raise ConflictError(
                "idempotency_key_reused", "该编辑请求标识已用于另一个事项。"
            )
        existing_body = existing.payload_json or {}
        existing_schedule_id = (
            existing.base_schedule_id
            or (existing_body.get("schedule") or {}).get("schedule_id")
        )
        existing_todos = existing_body.get("todos") or []
        existing_todo_id = (
            existing.base_todo_id
            or (existing_todos[0].get("todo_id") if existing_todos else None)
        )
        if payload.schedule_id and existing_schedule_id != payload.schedule_id:
            raise ConflictError(
                "idempotency_key_reused", "该编辑请求标识已用于另一条日程。"
            )
        if payload.todo_id and existing_todo_id != payload.todo_id:
            raise ConflictError(
                "idempotency_key_reused", "该编辑请求标识已用于另一条待办。"
            )
        return _serialize_draft(existing)
    timezone = await user_timezone(db, user, settings.matter_default_timezone)
    body: dict = {
        "kind": "update",
        "matter_id": matter.matter_id,
        "requires_choice": False,
        "title": None,
        "goal": None,
        "changes_note": None,
        "next_step": None,
        "schedule": None,
        "todos": [],
        "complete_todo_ids": [],
        "source_text": "直接编辑",
        "analysis": {},
    }
    base_schedule = None
    base_todo = None
    if payload.schedule_id:
        schedule = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == payload.schedule_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
            )
        )
        if schedule is None:
            raise NotFoundError("schedule_not_found", "日程不存在。")
        if schedule.matter_id != matter.matter_id:
            raise ConflictError("schedule_not_in_matter", "该日程不属于这个事项。")
        if not can_edit_schedule(schedule.source, schedule.data_source_id):
            raise InvalidRequestError(
                "schedule_not_editable", "设备导入的日程不能在应用内改写。"
            )
        base_schedule = schedule
        body["schedule"] = {
            "schedule_id": schedule.schedule_id,
            "title": schedule.title,
            "starts_at": schedule.starts_at,
            "ends_at": schedule.ends_at,
            "location": schedule.location,
            "latitude": schedule.latitude,
            "longitude": schedule.longitude,
        }
        body["changes_note"] = "修改日程"
    else:
        todo = await db.scalar(
            select(MatterTodo).where(
                MatterTodo.todo_id == payload.todo_id,
                MatterTodo.matter_id == matter.matter_id,
                MatterTodo.user_id == user.user_id,
            )
        )
        if todo is None:
            raise NotFoundError("todo_not_found", "待办不存在。")
        base_todo = todo
        body["todos"] = [
            {
                "todo_id": todo.todo_id,
                "title": todo.title,
                "due_at": todo.due_at,
                "reminder_at": todo.reminder_at,
                "status": todo.status,
            }
        ]
        body["changes_note"] = "修改待办时间"
    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        body,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(base_schedule.schedule_id if base_schedule else None),
    )
    draft = MatterDraft(
        user_id=user.user_id,
        kind="update",
        matter_id=matter.matter_id,
        status="preview",
        version=1,
        payload_json=_jsonable(body),
        conflict_json=_jsonable(conflict) if conflict else None,
        base_matter_version=matter.version,
        base_schedule_id=base_schedule.schedule_id if base_schedule else None,
        base_schedule_updated_at=base_schedule.updated_at if base_schedule else None,
        base_todo_id=base_todo.todo_id if base_todo else None,
        base_todo_updated_at=base_todo.updated_at if base_todo else None,
        origin_key=payload.idempotency_key,
    )
    db.add(draft)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        winner = await db.scalar(
            select(MatterDraft).where(
                MatterDraft.user_id == user.user_id,
                MatterDraft.origin_key == payload.idempotency_key,
            )
        )
        if winner is not None:
            return _serialize_draft(winner)
        raise
    await db.refresh(draft)
    return _serialize_draft(draft)


# ---------------------------------------------------------------------------
# today and reminders
# ---------------------------------------------------------------------------


async def today_view(
    db: AsyncSession, user: User, *, day: date, timezone: str
) -> dict:
    zone = ZoneInfo(timezone)
    starts = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    ends = starts + timedelta(days=1)
    schedules = list(
        (
            await db.scalars(
                select(ScheduleEvent)
                .where(
                    ScheduleEvent.user_id == user.user_id,
                    ScheduleEvent.deleted_at.is_(None),
                    ScheduleEvent.status.notin_(("cancelled", "deleted")),
                    ScheduleEvent.starts_at < ends,
                    or_(
                        ScheduleEvent.ends_at >= starts,
                        (ScheduleEvent.ends_at.is_(None))
                        & (ScheduleEvent.starts_at >= starts),
                    ),
                )
                .order_by(ScheduleEvent.starts_at)
            )
        ).all()
    )
    reminders = list(
        (
            await db.scalars(
                select(Reminder)
                .where(
                    Reminder.user_id == user.user_id,
                    Reminder.status == "pending",
                    Reminder.trigger_at >= starts,
                    Reminder.trigger_at < ends,
                )
                .order_by(Reminder.trigger_at)
            )
        ).all()
    )
    matter_ids = {row.matter_id for row in schedules if row.matter_id}
    matter_ids |= {row.matter_id for row in reminders if row.matter_id}
    matters = {
        row.matter_id: row
        for row in (
            await db.scalars(
                select(Matter).where(Matter.user_id == user.user_id)
            )
        ).all()
    }
    deadlines = list(
        (
            await db.scalars(
                select(MatterTodo)
                .join(Matter, Matter.matter_id == MatterTodo.matter_id)
                .where(
                    MatterTodo.user_id == user.user_id,
                    MatterTodo.status == "open",
                    MatterTodo.due_at.is_not(None),
                    MatterTodo.due_at < ends,
                    Matter.deleted_at.is_(None),
                    Matter.status.in_(("active", "waiting")),
                )
                .order_by(MatterTodo.due_at)
                .limit(50)
            )
        ).all()
    )
    matter_ids |= {row.matter_id for row in deadlines}

    preview_rows = list(
        (
            await db.scalars(
                select(MatterDraft)
                .where(
                    MatterDraft.user_id == user.user_id,
                    MatterDraft.status == "preview",
                )
                .order_by(MatterDraft.updated_at.desc())
                .limit(100)
            )
        ).all()
    )
    drafts = []
    for draft in preview_rows:
        body = draft.payload_json or {}
        if draft.matter_id and (
            draft.matter_id not in matters
            or matters[draft.matter_id].deleted_at is not None
        ):
            continue
        schedule = body.get("schedule")
        if schedule:
            start = _parse_dt(schedule.get("starts_at"))
            if start is None or not (starts <= start < ends):
                continue
            impact = "拟修改日程"
        else:
            created = _as_utc(draft.created_at)
            if created is None or not (starts <= created < ends):
                continue
            impact = "拟补充进展"
        conflict = draft.conflict_json or {}
        if conflict.get("has_conflict"):
            impact = "与已有安排冲突，需用户确认调整方案"
        drafts.append(
            {
                "draft_id": draft.draft_id,
                "matter_id": draft.matter_id,
                "matter_title": (
                    matters[draft.matter_id].title
                    if draft.matter_id and draft.matter_id in matters
                    else None
                ),
                "kind": draft.kind,
                "version": draft.version,
                "updated_at": draft.updated_at,
                "schedule": schedule,
                "conflict": conflict or None,
                "impact": impact,
            }
        )
        if draft.matter_id:
            matter_ids.add(draft.matter_id)

    changes = [
        row
        for row in (
            await db.scalars(
                select(MatterChange)
                .where(
                    MatterChange.user_id == user.user_id,
                    MatterChange.created_at >= starts,
                    MatterChange.created_at < ends,
                )
                .order_by(MatterChange.created_at.desc())
                .limit(50)
            )
        ).all()
        if row.matter_id in matters and matters[row.matter_id].deleted_at is None
    ]
    matter_ids |= {row.matter_id for row in changes}

    route = {
        "status": "unverified",
        "reason_code": "no_linked_route_result",
        "message": "尚无与该日程关联的路线结果；可打开事项后重新测算。",
        "advisory_id": None,
    }
    schedule_items = []
    for row in schedules:
        item = _serialize_schedule(row)
        item["matter_title"] = (
            matters[row.matter_id].title
            if row.matter_id and row.matter_id in matters
            else None
        )
        item["route"] = dict(route)
        schedule_items.append(item)
    reminder_items = []
    for row in reminders:
        item = _serialize_reminder(row)
        item["matter_title"] = (
            matters[row.matter_id].title
            if row.matter_id and row.matter_id in matters
            else None
        )
        reminder_items.append(item)
    change_items = []
    for change in changes:
        after = change.after_json or {}
        schedule = after.get("schedule") or {}
        parts = [
            part
            for part in (
                change.change_type,
                schedule.get("title"),
                schedule.get("starts_at"),
                schedule.get("location"),
            )
            if part
        ]
        change_items.append(
            {
                "change_id": change.change_id,
                "matter_id": change.matter_id,
                "matter_title": matters[change.matter_id].title,
                "change_type": change.change_type,
                "created_at": change.created_at,
                "summary": " / ".join(str(part) for part in parts),
            }
        )
    return {
        "date": day.isoformat(),
        "timezone": timezone,
        "schedules": schedule_items,
        "reminders": reminder_items,
        "pending_drafts": len(drafts),
        "deadlines": [
            {
                "todo_id": row.todo_id,
                "matter_id": row.matter_id,
                "matter_title": matters[row.matter_id].title
                if row.matter_id in matters
                else "",
                "title": row.title,
                "due_at": row.due_at,
                "status": row.status,
                "reminder_at": row.reminder_at,
            }
            for row in deadlines
        ],
        "drafts": drafts,
        "changes": change_items,
    }


async def list_reminders(
    db: AsyncSession,
    user: User,
    *,
    starts_at: datetime | None,
    ends_at: datetime | None,
    status: str | None,
) -> list[dict]:
    query = select(Reminder).where(Reminder.user_id == user.user_id)
    if starts_at is not None:
        query = query.where(Reminder.trigger_at >= ensure_aware(starts_at))
    if ends_at is not None:
        query = query.where(Reminder.trigger_at < ensure_aware(ends_at))
    if status is not None:
        query = query.where(Reminder.status == status)
    rows = list((await db.scalars(query.order_by(Reminder.trigger_at))).all())
    return [_serialize_reminder(row) for row in rows]
