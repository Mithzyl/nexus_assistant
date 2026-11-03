"""Persistent follow-up instructions, individual executions and transactional record events."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from assistant_api.models.base import Base, TimestampMixin, new_id


class FollowUp(TimestampMixin, Base):
    """Reference existing business records and retain only follow-up instructions and timing."""
    __tablename__ = 'follow_ups'
    __table_args__ = (UniqueConstraint('user_id','idempotency_key',name='uq_followup_user_key'), Index('ix_followups_due','state','next_trigger_at'))
    follow_up_id: Mapped[str] = mapped_column(String(36),primary_key=True,default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id',ondelete='CASCADE'),index=True)
    matter_id: Mapped[str] = mapped_column(ForeignKey('matters.matter_id',ondelete='CASCADE'),index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(180))
    instruction: Mapped[str] = mapped_column(Text)
    creation_json: Mapped[dict] = mapped_column(JSON,default=dict)
    state: Mapped[str] = mapped_column(String(16),default='enabled')
    execution_mode: Mapped[str] = mapped_column(String(16),default='evaluate')
    version: Mapped[int] = mapped_column(Integer,default=1)
    references_json: Mapped[list] = mapped_column(JSON)
    trigger_json: Mapped[dict] = mapped_column(JSON)
    stop_when_json: Mapped[list] = mapped_column(JSON,default=list)
    observed_json: Mapped[dict] = mapped_column(JSON,default=dict)
    next_trigger_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_trigger_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FollowUpExecution(TimestampMixin, Base):
    """One claimed trigger with stable keys and a user-visible intake result reference."""
    __tablename__ = 'follow_up_executions'
    __table_args__ = (UniqueConstraint('follow_up_id','trigger_key',name='uq_followup_trigger'),Index('ix_followup_execution_queue','state','created_at'))
    execution_id: Mapped[str] = mapped_column(String(36),primary_key=True,default=new_id)
    follow_up_id: Mapped[str] = mapped_column(ForeignKey('follow_ups.follow_up_id',ondelete='CASCADE'),index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.user_id',ondelete='CASCADE'),index=True)
    trigger_key: Mapped[str] = mapped_column(String(180))
    rule_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24),default='queued')
    local_run_id: Mapped[str | None] = mapped_column(String(36))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))


class BusinessChangeEvent(TimestampMixin, Base):
    """Record a committed owned-record mutation for follow-up re-evaluation."""
    __tablename__ = 'business_change_events'
    event_id: Mapped[str] = mapped_column(String(36),primary_key=True,default=new_id)
    user_id: Mapped[str] = mapped_column(String(36),index=True)
    resource: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(128))
    changed_fields_json: Mapped[list] = mapped_column(JSON)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),index=True)
    cause_execution_id: Mapped[str | None] = mapped_column(String(36))
