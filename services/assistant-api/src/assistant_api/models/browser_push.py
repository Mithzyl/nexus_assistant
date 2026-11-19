"""Private browser subscriptions and durable per-occurrence notification deliveries."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from assistant_api.models.base import Base, TimestampMixin, new_id


class BrowserPushSubscription(TimestampMixin, Base):
    """Bind a browser-managed push capability to one active Assistant account."""
    __tablename__ = 'browser_push_subscriptions'
    subscription_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys_json: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrowserPushDelivery(TimestampMixin, Base):
    """Claim each scheduled occurrence once; transport acceptance is separate from display."""
    __tablename__ = 'browser_push_deliveries'
    __table_args__ = (UniqueConstraint('subscription_id', 'occurrence', name='uq_browser_push_occurrence'),)
    delivery_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subscription_id: Mapped[str] = mapped_column(ForeignKey('browser_push_subscriptions.subscription_id'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    occurrence: Mapped[str] = mapped_column(String(180))
    resource_kind: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str] = mapped_column(String(36))
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    displayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(48))
