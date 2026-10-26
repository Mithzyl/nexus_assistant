"""User-authorized device content synchronized into PA's own database.

The system calendar/reminders/notes remain the source of truth. These tables are
an uploaded copy with explicit source, scope, version and tombstone metadata.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id, utc_now


class ContentSource(TimestampMixin, Base):
    """One selected device/source and its two independent permissions."""

    __tablename__ = "content_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", "source_type", "display_name",
                         name="uq_content_source_device_identity"),
    )

    source_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(24))
    device_id: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(120))
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    assistant_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    upload_fields_json: Mapped[list] = mapped_column(JSON, default=list)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(24))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class ContentSourceScope(TimestampMixin, Base):
    """Track selected scopes without treating out-of-scope rows as deleted."""

    __tablename__ = "content_source_scopes"
    __table_args__ = (UniqueConstraint("source_id", "scope_key", name="uq_source_scope"),)

    scope_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("content_sources.source_id", ondelete="CASCADE"), index=True
    )
    scope_key: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Re-adding a scope must not expose stale rows uploaded before it was removed.
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ContentItem(TimestampMixin, Base):
    """One uploaded item, usually keyed by the provider's stable external id."""

    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint("source_id", "kind", "external_id", name="uq_content_external"),
        Index("ix_content_user_kind", "user_id", "kind"),
        Index("ix_content_source_scope", "source_id", "scope_key"),
    )

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("content_sources.source_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    external_id: Mapped[str] = mapped_column(String(255))
    scope_key: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(255))
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ContentSyncBatch(TimestampMixin, Base):
    """Make a repeated upload batch a no-op and retain only aggregate counts."""

    __tablename__ = "content_sync_batches"
    __table_args__ = (UniqueConstraint("source_id", "batch_id", name="uq_source_batch"),)

    sync_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("content_sources.source_id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[str] = mapped_column(String(128))
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    # Hash of the submitted payload; lets a reused batch id with different
    # content be rejected instead of silently returning the first result.
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
