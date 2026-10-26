"""Persistent matters, their todos, progress, drafts and reminders.

Why not reuse `tasks`? `tasks` is a standalone task list: title/status/due date and
no matter link, goal, ordering, progress history or reminder relationship. Adding
all of that would silently change the meaning of existing task rows and the
read-only task tool result. Matters therefore get their own tables, while the
existing `schedules` table is extended in place (it already models "appointment
with time and place").

Why not reuse `notification_outbox`? That table is bound to a commute plan,
instance and reminder sequence, and is delivered by the commute worker. A general
reminder has a different lifecycle; delivery is not implemented in this round and
is recorded as an open product question.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id, utc_now

MATTER_STATUSES = ("active", "waiting", "archived", "deleted")
TODO_STATUSES = ("open", "done")
DRAFT_STATUSES = ("preview", "confirmed", "cancelled")
SYNC_STATUSES = ("local_only", "pending_ios", "ios_synced")


class Matter(TimestampMixin, Base):
    """A multi-step thing the user is carrying; the container for its records."""

    __tablename__ = "matters"
    __table_args__ = (
        Index("ix_matters_user_status_updated", "user_id", "status", "updated_at"),
    )

    matter_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    next_step: Mapped[str | None] = mapped_column(String(500))
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatterTodo(TimestampMixin, Base):
    """A checklist item inside a matter; `due_at` is a deadline, not a reminder."""

    __tablename__ = "matter_todos"
    __table_args__ = (Index("ix_matter_todos_matter", "matter_id", "position"),)

    todo_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.matter_id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="open")
    position: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatterProgress(TimestampMixin, Base):
    """Append-only history: what the user said and what the assistant recorded."""

    __tablename__ = "matter_progress"
    __table_args__ = (Index("ix_matter_progress_matter_created", "matter_id", "created_at"),)

    progress_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.matter_id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="progress")
    source_run_id: Mapped[str | None] = mapped_column(String(36))


class MatterDraft(TimestampMixin, Base):
    """A proposed change; nothing formal is written before confirmation."""

    __tablename__ = "matter_drafts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "confirm_key", name="uq_matter_drafts_user_confirm"
        ),
        UniqueConstraint(
            "user_id", "origin_key", name="uq_matter_drafts_user_origin"
        ),
        Index("ix_matter_drafts_user_status", "user_id", "status", "updated_at"),
    )

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    matter_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="preview")
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    conflict_json: Mapped[dict | None] = mapped_column(JSON)
    candidates_json: Mapped[list | None] = mapped_column(JSON)
    base_matter_version: Mapped[int | None] = mapped_column(Integer)
    base_schedule_id: Mapped[str | None] = mapped_column(String(36))
    base_schedule_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_run_id: Mapped[str | None] = mapped_column(String(36))
    confirm_key: Mapped[str | None] = mapped_column(String(128))
    # Idempotency key for a directly created edit draft (no model run).
    origin_key: Mapped[str | None] = mapped_column(String(128))
    result_json: Mapped[dict | None] = mapped_column(JSON)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatterChange(TimestampMixin, Base):
    """What a confirmation actually changed, for correction and revert."""

    __tablename__ = "matter_changes"
    __table_args__ = (Index("ix_matter_changes_matter", "matter_id", "created_at"),)

    change_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    matter_id: Mapped[str] = mapped_column(
        ForeignKey("matters.matter_id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    draft_id: Mapped[str | None] = mapped_column(String(36))
    change_type: Mapped[str] = mapped_column(String(24))
    before_json: Mapped[dict] = mapped_column(JSON)
    after_json: Mapped[dict] = mapped_column(JSON)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reminder(TimestampMixin, Base):
    """A stored reminder record. Delivery is intentionally not implemented yet."""

    __tablename__ = "reminders"
    __table_args__ = (
        Index("ix_reminders_user_trigger", "user_id", "trigger_at"),
        UniqueConstraint(
            "user_id", "source", "data_source_id", "source_external_id",
            name="uq_reminders_user_source_device_external",
        ),
    )

    reminder_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    matter_id: Mapped[str | None] = mapped_column(String(36))
    todo_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(String(500))
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    sync_status: Mapped[str | None] = mapped_column(String(24))
    source: Mapped[str | None] = mapped_column(String(24))
    source_external_id: Mapped[str | None] = mapped_column(String(255))
    source_scope_key: Mapped[str | None] = mapped_column(String(255))
    data_source_id: Mapped[str | None] = mapped_column(String(36))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[dict | None] = mapped_column(JSON)


__all__ = [
    "Matter",
    "MatterChange",
    "MatterDraft",
    "MatterProgress",
    "MatterTodo",
    "MATTER_STATUSES",
    "Reminder",
    "SYNC_STATUSES",
    "TODO_STATUSES",
]
