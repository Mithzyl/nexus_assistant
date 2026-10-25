"""Declarative base, identifiers and shared column mixins."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    """Return the 36-character identifier format already used by NexusPilot rows."""

    return str(uuid4())


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp; never use naive local time."""

    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive value.

    SQLite does not preserve timezone offsets even for timezone-aware columns, so
    every comparison goes through this helper instead of assuming awareness.
    """

    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Single metadata root for this service's own database."""


class TimestampMixin:
    """Track creation and last mutation without relying on database triggers."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
