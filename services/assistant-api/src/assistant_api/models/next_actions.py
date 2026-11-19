"""Store recurring business availability, the latest Today snapshot and feedback."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin


class UserAvailability(TimestampMixin, Base):
    """Store one explicit weekly schedule per account without modifying legacy preferences."""
    __tablename__ = 'user_availability'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64))
    windows: Mapped[list] = mapped_column(JSON)


class NextActionState(TimestampMixin, Base):
    """Keep a bounded set of feedback and at most one live recommendation per account."""
    __tablename__ = 'next_action_states'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=0)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict | None] = mapped_column(JSON)
    feedback: Mapped[dict] = mapped_column(JSON, default=dict)


class NextActionCommandReceipt(TimestampMixin, Base):
    """Preserve an exact per-account response after a repeat or concurrent click."""
    __tablename__ = 'next_action_command_receipts'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
