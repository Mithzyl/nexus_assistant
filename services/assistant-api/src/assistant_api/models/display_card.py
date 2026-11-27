"""Persist immutable conversation cards and the latest card of an owned matter."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class DisplayCard(TimestampMixin, Base):
    """One saved presentation, with a unique matter slot only for progress."""

    __tablename__ = "display_cards"
    __table_args__ = (UniqueConstraint("user_id", "progress_matter_id"),)

    card_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    matter_id: Mapped[str | None] = mapped_column(ForeignKey("matters.matter_id", ondelete="CASCADE"))
    progress_matter_id: Mapped[str | None] = mapped_column(String(36))
    source_run_id: Mapped[str] = mapped_column(String(36))
    source_mode: Mapped[str] = mapped_column(String(24))
    creation_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    card_type: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(default=1)
    content_json: Mapped[dict] = mapped_column(JSON)
    notify_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
