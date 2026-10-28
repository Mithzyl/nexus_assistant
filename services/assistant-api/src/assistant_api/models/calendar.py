"""Private calendar-feed subscriptions for confirmed app-native schedules."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class CalendarSubscription(TimestampMixin, Base):
    """One revocable feed credential and its explicitly selected matters."""

    __tablename__ = "calendar_subscriptions"

    subscription_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), unique=True, index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    matter_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["CalendarSubscription"]
