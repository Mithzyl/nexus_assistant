"""Per-account Bark binding and durable notification delivery state."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class BarkReminderSetting(TimestampMixin, Base):
    """Per-account auto-reminder switch and last explicit test outcome."""

    __tablename__ = "bark_reminder_settings"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    # Development-stage plaintext storage. Never selected by a generic API
    # serializer, never logged, and never returned to the App.
    device_key: Mapped[str | None] = mapped_column(Text)
    auto_reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    assistant_notifications_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(24))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BarkNotificationDelivery(TimestampMixin, Base):
    """Claim one Assistant notification per account and business occurrence."""

    __tablename__ = "bark_notification_deliveries"
    __table_args__ = (UniqueConstraint("user_id", "occurrence", name="uq_bark_notification_occurrence"),)

    delivery_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    occurrence: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(24), default="sending", index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(48))
