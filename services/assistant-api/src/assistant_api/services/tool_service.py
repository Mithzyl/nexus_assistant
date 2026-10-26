"""Read-only business tools that NexusPilot may call during an Agent run.

The current App tool HTTP envelope carries identity from the authenticated Run,
never from model arguments. Arguments can only narrow a query. Results are shaped
as NexusPilot's ``BusinessReadResult``: ``items`` (max 20) plus ``next_cursor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.core.config import Settings
from assistant_api.core.errors import InvalidRequestError, NotFoundError
from assistant_api.models import ContentSource, Note, ScheduleEvent, TaskItem
from assistant_api.models.base import as_utc
from assistant_api.services.data_source_service import assistant_content

MAX_APP_RESULT_ITEMS = 20


class ToolArguments(BaseModel):
    """Reject any undeclared argument, including an attempted user id."""

    model_config = ConfigDict(extra="forbid")


class ListTasksArguments(ToolArguments):
    """Match the current NexusPilot task-list tool schema."""

    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=1024)
    status: str | None = Field(default=None, pattern=r"^(open|done|archived)$")


class SearchNotesArguments(ToolArguments):
    """Match the current NexusPilot note-search tool schema."""

    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=1024)


class CalendarArguments(ToolArguments):
    """Match the current NexusPilot calendar tool schema."""

    start_date: date
    end_date: date
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=1024)


@dataclass(frozen=True)
class ToolSpec:
    """Describe one callable tool and its local handler."""

    name: str
    description: str
    arguments: type[ToolArguments]
    handler: Callable[..., Any]


async def list_user_tasks(
    db: AsyncSession, user_id: str, args: ListTasksArguments, _: Settings
):
    """Merge PA-native tasks with authorized, user-selected reminders."""

    statement = select(TaskItem).where(TaskItem.user_id == user_id)
    if args.status:
        statement = statement.where(TaskItem.status == args.status)
    rows = (
        await db.scalars(
            statement.order_by(TaskItem.due_at.is_(None), TaskItem.due_at).limit(args.limit)
        )
    ).all()
    items: list[dict[str, Any]] = [
        {
            "task_id": row.task_id,
            "title": row.title,
            "status": row.status,
            "due_at": row.due_at.isoformat() if row.due_at else None,
        }
        for row in rows
    ]
    for item, source in await assistant_content(db, user_id, "reminder", 50):
        if args.status and item.status != args.status:
            continue
        allowed = set(source.upload_fields_json or [])
        items.append(
            {
                "task_id": f"content:{item.item_id}",
                "title": item.title if "title" in allowed else "",
                "status": item.status if "status" in allowed else None,
                "due_at": item.due_at.isoformat() if item.due_at and "due" in allowed else None,
                "source": "device_reminder",
            }
        )
    items.sort(key=lambda value: (value["due_at"] is None, value["due_at"] or ""))
    return {"items": items[: args.limit], "next_cursor": None}


async def search_user_notes(
    db: AsyncSession, user_id: str, args: SearchNotesArguments, _: Settings
):
    """Search native notes and authorized, user-selected device notes."""

    term = f"%{args.query}%"
    rows = (
        await db.scalars(
            select(Note)
            .where(
                Note.user_id == user_id,
                or_(Note.title.like(term), Note.body.like(term)),
            )
            .order_by(Note.updated_at.desc())
            .limit(args.limit)
        )
    ).all()
    items: list[dict[str, Any]] = [
        {
            "note_id": row.note_id,
            "title": row.title,
            "excerpt": row.body[:160],
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]
    needle = args.query.casefold()
    for item, source in await assistant_content(db, user_id, "note", 50):
        allowed = set(source.upload_fields_json or [])
        title = item.title if "title" in allowed else ""
        body = item.body if ({"body", "notes"} & allowed) else ""
        if needle not in title.casefold() and needle not in body.casefold():
            continue
        items.append(
            {
                "note_id": f"content:{item.item_id}",
                "title": title,
                "excerpt": body[:160],
                "updated_at": item.source_updated_at.isoformat(),
                "source": "device_note",
            }
        )
    items.sort(key=lambda value: value["updated_at"], reverse=True)
    return {"items": items[: args.limit], "next_cursor": None}


async def get_calendar_events(
    db: AsyncSession, user_id: str, args: CalendarArguments, _: Settings
):
    """Merge PA-native calendar entries with authorized device calendars."""

    if args.end_date < args.start_date:
        raise InvalidRequestError("invalid_window", "结束日期必须不早于开始日期。")
    start_at = datetime.combine(args.start_date, time.min, tzinfo=UTC)
    end_at = datetime.combine(args.end_date + timedelta(days=1), time.min, tzinfo=UTC)
    authorized_sources = select(ContentSource.source_id).where(
        ContentSource.user_id == user_id,
        ContentSource.assistant_enabled.is_(True),
    )
    rows = (
        await db.scalars(
            select(ScheduleEvent)
            .where(
                ScheduleEvent.user_id == user_id,
                ScheduleEvent.deleted_at.is_(None),
                ScheduleEvent.status.notin_(("cancelled", "deleted")),
                ScheduleEvent.starts_at >= start_at,
                ScheduleEvent.starts_at < end_at,
                or_(
                    ScheduleEvent.data_source_id.is_(None),
                    ScheduleEvent.data_source_id.in_(authorized_sources),
                ),
            )
            .order_by(ScheduleEvent.starts_at)
            .limit(args.limit)
        )
    ).all()
    items: list[dict[str, Any]] = [
        {
            "schedule_id": row.schedule_id,
            "title": row.title,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat() if row.ends_at else None,
            "location": row.location,
            "status": row.status,
        }
        for row in rows
    ]
    for item, source in await assistant_content(db, user_id, "calendar_event", 50):
        if item.starts_at is None:
            continue
        started = as_utc(item.starts_at)
        if not (start_at <= started < end_at):
            continue
        allowed = set(source.upload_fields_json or [])
        items.append(
            {
                "schedule_id": f"content:{item.item_id}",
                "title": item.title if "title" in allowed else "",
                "starts_at": started.isoformat(),
                "ends_at": as_utc(item.ends_at).isoformat()
                if item.ends_at and "end" in allowed
                else None,
                "location": item.location if "location" in allowed else None,
                "status": item.status if "status" in allowed else None,
                "source": "device_calendar",
            }
        )
    items.sort(key=lambda value: value["starts_at"])
    return {"items": items[: args.limit], "next_cursor": None}


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            name="personal_assistant.list_tasks",
            description="List the calling user's tasks with status and due date.",
            arguments=ListTasksArguments,
            handler=list_user_tasks,
        ),
        ToolSpec(
            name="personal_assistant.search_notes",
            description="Search the calling user's notes by literal text.",
            arguments=SearchNotesArguments,
            handler=search_user_notes,
        ),
        ToolSpec(
            name="personal_assistant.list_calendar_events",
            description="List the calling user's calendar entries in a date interval.",
            arguments=CalendarArguments,
            handler=get_calendar_events,
        ),
    )
}


def tool_definitions() -> list[dict[str, Any]]:
    """Publish accepted canonical names and schemas; no identity field is exposed."""

    return [
        {
            "name": spec.name,
            "version": "1.0.0",
            "description": spec.description,
            "input_schema": spec.arguments.model_json_schema(),
        }
        for spec in TOOLS.values()
    ]


async def execute_tool(
    db: AsyncSession,
    *,
    user_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Validate arguments, enforce ownership, and return BusinessReadResult JSON."""

    spec = TOOLS.get(tool_name)
    if spec is None:
        raise NotFoundError("unknown_tool", "工具不存在。")
    try:
        parsed = spec.arguments.model_validate(arguments)
    except ValidationError as exc:
        raise InvalidRequestError(
            "invalid_tool_arguments",
            "工具参数无效：" + ", ".join(
                f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
                for error in exc.errors(include_input=False)[:6]
            ),
        ) from exc

    result = await spec.handler(db, user_id, parsed, settings)
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        items = []
    limit = min(settings.tool_result_max_items, MAX_APP_RESULT_ITEMS)
    return {
        "items": items[:limit],
        "next_cursor": result.get("next_cursor") if isinstance(result, dict) else None,
    }


__all__ = ["TOOLS", "execute_tool", "tool_definitions"]
