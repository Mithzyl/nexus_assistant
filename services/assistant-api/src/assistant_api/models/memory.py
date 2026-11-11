"""Assistant-owned encrypted memories, immutable revisions and durable commands."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


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
