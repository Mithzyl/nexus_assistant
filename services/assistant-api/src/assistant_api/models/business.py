"""Business records owned exclusively by Personal Assistant.

NexusPilot never reads these tables. It sees them only as tool results that this
service has already filtered by the authenticated user.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class TaskItem(TimestampMixin, Base):
    """Represent a user task; `source_run_id` records which AI run created it."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_user_status_due", "user_id", "status", "due_at"),)

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_run_id: Mapped[str | None] = mapped_column(String(36))


class ScheduleEvent(TimestampMixin, Base):
    """Represent a calendar entry; the business record, not a memory item.

    The table existed before matters. The columns below are additive and stay
    NULL/NULL-compatible for old rows; `source_run_id` keeps its original meaning
    ("which AI run created this") and is not reused for the new record origin.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_user_start", "user_id", "starts_at"),
        Index("ix_schedules_matter", "matter_id"),
        UniqueConstraint(
            "user_id", "source", "data_source_id", "source_external_id",
            name="uq_schedules_user_source_device_external",
        ),
    )

    schedule_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    source_run_id: Mapped[str | None] = mapped_column(String(36))

    # Record origin: web_native | ios_eventkit. Not the same as source_run_id or
    # ContentSource.source_type (calendar/reminders/notes).
    source: Mapped[str | None] = mapped_column(String(24))
    # local_only | pending_ios | ios_synced. See docs for the unresolved mapping
    # to the product text's `web_confirmed_pending_ios`.
    sync_status: Mapped[str | None] = mapped_column(String(24))
    source_external_id: Mapped[str | None] = mapped_column(String(255))
    source_scope_key: Mapped[str | None] = mapped_column(String(255))
    # Incoming Apple `last_modified_at`; `updated_at` remains the server edit time.
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[int | None] = mapped_column(Integer)
    matter_id: Mapped[str | None] = mapped_column(String(36))
    data_source_id: Mapped[str | None] = mapped_column(String(36))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Note(TimestampMixin, Base):
    """Represent a free-text note searchable by the read-only note tools."""

    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_user_updated", "user_id", "updated_at"),)

    note_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list] = mapped_column(JSON, default=list)


class BusinessRecord(TimestampMixin, Base):
    """Represent an expense or other ledger entry with an explicit amount."""

    __tablename__ = "business_records"
    __table_args__ = (
        Index("ix_business_records_user_occurred", "user_id", "occurred_at"),
    )

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source_run_id: Mapped[str | None] = mapped_column(String(36))
