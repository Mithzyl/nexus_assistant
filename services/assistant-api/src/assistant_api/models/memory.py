"""Assistant-owned encrypted memories, immutable revisions and durable commands."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class MemoryScope(TimestampMixin, Base):
    """Keep account-wide automatic-use settings separate from individual entry revisions."""
    __tablename__ = 'assistant_memory_scopes'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scope_revision: Mapped[int] = mapped_column(Integer, default=0)


class MemoryEntry(TimestampMixin, Base):
    """Store current metadata; body, source quotes and extensions are encrypted together."""
    __tablename__ = 'assistant_memory_entries'
    entry_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    entry_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default='active')
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_type: Mapped[str] = mapped_column(String(32), default='free_text')
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[bytes] = mapped_column(LargeBinary)


class MemoryVersion(TimestampMixin, Base):
    """Retain each exact encrypted revision, including forgotten state and its provenance."""
    __tablename__ = 'assistant_memory_versions'
    entry_id: Mapped[str] = mapped_column(ForeignKey('assistant_memory_entries.entry_id'), primary_key=True)
    entry_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    sensitive: Mapped[bool] = mapped_column(Boolean)
    memory_type: Mapped[str] = mapped_column(String(32))
    body: Mapped[bytes] = mapped_column(LargeBinary)


class MemoryOperation(TimestampMixin, Base):
    """Persist exact encrypted requests and post-answer work; one owner/key has one result."""
    __tablename__ = 'assistant_memory_operations'
    __table_args__ = (UniqueConstraint('user_id', 'key', name='uq_assistant_memory_operation_key'),)
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    key: Mapped[str] = mapped_column(String(128))
    local_run_id: Mapped[str | None] = mapped_column(ForeignKey('nexus_runs.local_run_id'), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    request_body: Mapped[bytes] = mapped_column(LargeBinary)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BusinessMemoryApplication(TimestampMixin, Base):
    """Retain immutable encrypted field provenance in the same transaction as the actual business write."""
    __tablename__ = 'business_memory_applications'
    __table_args__ = (UniqueConstraint('user_id', 'command_key', 'target_kind', 'target_id',
                                     name='uq_business_memory_application'),)
    application_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id'), index=True)
    command_key: Mapped[str] = mapped_column(String(128))
    target_kind: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    target_revision: Mapped[int] = mapped_column(Integer)
    body: Mapped[bytes] = mapped_column(LargeBinary)
