"""Matter lifecycle, preview/confirm, today view and iOS sync support.

The confirmation boundary is enforced here, not in the web button: formal
schedules and reminders are only written inside `confirm_draft`, after the caller
proves the preview version and (when needed) acknowledges a conflict.
"""

from __future__ import annotations

import json
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
from assistant_api.services.matter_conflicts import evaluate_conflict


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
        "title": schedule.title,
        "starts_at": _as_utc(schedule.starts_at).isoformat() if schedule.starts_at else None,
        "ends_at": _as_utc(schedule.ends_at).isoformat() if schedule.ends_at else None,
        "location": schedule.location,
        "latitude": schedule.latitude,
        "longitude": schedule.longitude,
        "deleted_at": _as_utc(schedule.deleted_at).isoformat() if schedule.deleted_at else None,
    }


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
) -> tuple[dict | None, bool, str]:
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
        return _serialize_draft(existing), True, "matter"

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
        # A question must not silently become an empty matter. The web should
        # send it to POST /asks; this endpoint returns no draft.
        return None, False, "question"
    if parsed.get("intent") == "unclear":
        raise InvalidRequestError(
            "matter_intent_unclear",
            "这句话还不足以形成事项，请补充目标、时间或要办的事。",
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
    return _serialize_draft(draft), False, "matter"


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
        conflict = await _conflict_for_payload(
            db,
            runtime,
            user,
            payload,
            settings=settings,
            timezone=timezone,
            exclude_schedule_id=payload["schedule"].get("schedule_id"),
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
    body = dict(draft.payload_json or {})
    body["matter_id"] = matter.matter_id
    body["requires_choice"] = False
    body["kind"] = "update"
    # A previously chosen adjustment was computed for the old candidate; it
    # must not survive this re-binding.
    body.pop("selected_option", None)
    body.pop("previous_adjustment", None)
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
    body = dict(draft.payload_json or {})
    conflict = await _conflict_for_payload(
        db,
        runtime,
        user,
        body,
        settings=settings,
        timezone=timezone,
        exclude_schedule_id=(body.get("schedule") or {}).get("schedule_id"),
    )
    options = {option["option_id"]: option for option in (conflict or {}).get("options", [])}
    option = options.get(payload.option_id)
    if option is None:
        raise InvalidRequestError(
            "option_not_available", "当前预览没有这个调整方案，请重新计算冲突。"
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
        delta = timedelta(seconds=int(option.get("shift_seconds") or 0))
        base_updated = target.updated_at
        body["previous_adjustment"] = {
            "schedule_id": target.schedule_id,
            "shift_seconds": int(option.get("shift_seconds") or 0),
            "base_updated_at": _jsonable(base_updated),
        }
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
    merged = dict(draft.payload_json or {})
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
            if schedule_row.source not in (None, "web_native"):
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
            base = _parse_dt(adjustment["base_updated_at"], timezone)
            if ensure_aware(target.updated_at) != base:
                raise ConflictError("schedule_changed", "相邻日程已变化，请重新选择方案。")
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

        now = datetime.now(UTC)
        before = {"matter": None, "schedule": None}
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

        reminders_created: list[Reminder] = []
        position = len(existing_todos)
        for item in payload.get("todos") or []:
            todo_id = item.get("todo_id")
            if todo_id and todo_id in existing_todos:
                todo = existing_todos[todo_id]
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
            pending = await db.scalar(
                select(Reminder).where(
                    Reminder.user_id == user.user_id,
                    Reminder.todo_id == todo.todo_id,
                    Reminder.status == "pending",
                )
            )
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
                reminders_created.append(reminder)
            else:
                pending.trigger_at = todo.reminder_at
                pending.title = f"提醒：{todo.title}"

        for todo_id in payload.get("complete_todo_ids") or []:
            todo = existing_todos.get(todo_id)
            if todo is not None:
                todo.status = "done"
                todo.completed_at = now

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
                    "schedule": _schedule_snapshot(schedule_row),
                    "reminder_ids": [reminder.reminder_id for reminder in reminders_created],
                    "completed_todo_ids": payload.get("complete_todo_ids") or [],
                }
            ),
        )
        db.add(change)
        await db.flush()
        draft.confirm_key = idempotency_key
        draft.result_json = {
            "matter_id": matter.matter_id,
            "schedule_id": schedule_row.schedule_id if schedule_row else None,
            "reminder_ids": [reminder.reminder_id for reminder in reminders_created],
            "change_id": change.change_id,
            "conflict": _jsonable(conflict) if conflict else None,
        }
        await db.commit()
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
            MatterChange.matter_id == matter.matter_id,
            MatterChange.user_id == user.user_id,
        )
    )
    if change is None:
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
    before = change.before_json or {}
    after = change.after_json or {}
    now = datetime.now(UTC)

    after_schedule = after.get("schedule")
    before_schedule = before.get("schedule")
    if after_schedule:
        current = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == after_schedule["schedule_id"],
                ScheduleEvent.user_id == user.user_id,
            )
        )
        if current is None:
            raise ConflictError("schedule_changed", "关联日程已不存在。")
        same = (
            current.title == after_schedule["title"]
            and _as_utc(current.starts_at)
            == _parse_dt(after_schedule["starts_at"])
            and _as_utc(current.ends_at)
            == (
                _parse_dt(after_schedule["ends_at"])
                if after_schedule.get("ends_at")
                else None
            )
            and current.location == after_schedule.get("location")
        )
        if not same:
            raise ConflictError("schedule_changed", "关联日程已被修改，不能覆盖撤回。")
        if before_schedule is None:
            result = await db.execute(
                update(ScheduleEvent)
                .where(
                    ScheduleEvent.schedule_id == current.schedule_id,
                    ScheduleEvent.user_id == user.user_id,
                )
                .values(status="cancelled", deleted_at=now)
            )
        else:
            result = await db.execute(
                update(ScheduleEvent)
                .where(
                    ScheduleEvent.schedule_id == current.schedule_id,
                    ScheduleEvent.user_id == user.user_id,
                )
                .values(
                    title=before_schedule["title"],
                    starts_at=_parse_dt(before_schedule["starts_at"]),
                    ends_at=_parse_dt(before_schedule.get("ends_at")),
                    location=before_schedule.get("location"),
                    latitude=before_schedule.get("latitude"),
                    longitude=before_schedule.get("longitude"),
                    sync_status="local_only",
                    deleted_at=None,
                )
            )
        if result.rowcount == 0:
            raise ConflictError("schedule_changed", "关联日程已被修改，不能覆盖撤回。")

    for reminder_id in after.get("reminder_ids") or []:
        await db.execute(
            update(Reminder)
            .where(Reminder.reminder_id == reminder_id, Reminder.status == "pending")
            .values(status="cancelled", cancelled_at=now)
        )
    for todo_id in after.get("completed_todo_ids") or []:
        await db.execute(
            update(MatterTodo)
            .where(
                MatterTodo.todo_id == todo_id,
                MatterTodo.matter_id == matter.matter_id,
                MatterTodo.user_id == user.user_id,
                # Later manual completion must not be overwritten by this revert.
                MatterTodo.completed_at.is_not(None),
                MatterTodo.completed_at <= change.created_at,
            )
            .values(status="open", completed_at=None)
        )

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
    pending_drafts = await db.scalar(
        select(func.count(MatterDraft.draft_id)).where(
            MatterDraft.user_id == user.user_id,
            MatterDraft.status == "preview",
        )
    )
    return {
        "date": day.isoformat(),
        "timezone": timezone,
        "schedules": [_serialize_schedule(row) for row in schedules],
        "reminders": [_serialize_reminder(row) for row in reminders],
        "pending_drafts": int(pending_drafts or 0),
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
