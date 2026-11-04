"""Durable user supplements, their AI jobs and separately attributed text versions."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class MatterSupplement(TimestampMixin, Base):
    """Save a user submission and its queued analysis in the same transaction."""

    __tablename__ = "matter_supplements"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_supplement_user_request"),
        Index("ix_supplement_matter_created", "user_id", "matter_id", "created_at"),
        Index("ix_supplement_queue", "ai_status", "created_at"),
    )
    supplement_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    matter_id: Mapped[str] = mapped_column(ForeignKey("matters.matter_id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_json: Mapped[dict] = mapped_column(JSON)
    ai_status: Mapped[str] = mapped_column(String(24), default="queued")
    classification: Mapped[str] = mapped_column(String(24), default="unknown")
    analysis_attempt: Mapped[int] = mapped_column(Integer, default=0)
    local_run_id: Mapped[str | None] = mapped_column(String(36))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class SupplementText(TimestampMixin, Base):
    """Keep immutable authorship and explicit correction/answer links; null links retain legacy ambiguity."""

    __tablename__ = "supplement_texts"
    __table_args__ = (
        Index("ix_supplement_text_parent", "supplement_id", "created_at"),
        Index("ix_supplement_text_relations", "parent_text_id", "relation_type", "created_at"),
    )
    text_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplement_id: Mapped[str] = mapped_column(ForeignKey("matter_supplements.supplement_id", ondelete="CASCADE"))
    origin: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    parent_text_id: Mapped[str | None] = mapped_column(ForeignKey("supplement_texts.text_id", ondelete="SET NULL"))
    relation_type: Mapped[str | None] = mapped_column(String(24))
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id"))
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey("nexus_runs.local_run_id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id"))
