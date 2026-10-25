"""Private single-account Bark channel state (no key material in the database)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin


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
    last_test_status: Mapped[str | None] = mapped_column(String(24))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
