"""Local references to NexusPilot executions, plus the chat association.

`nexus_runs` stays the only execution record: it holds the Nexus run/workflow
identifiers, the status projection, and the model result. It is also the shared
task record for every AI purpose (chat, commute analysis, and future ones).

`user_asks` is deliberately thin: it means "a user asked this in chat". A run
that exists only for an internal job (for example a commute analysis) has no
`user_asks` row and therefore never appears in chat history. Chat status and
answer are always read from `nexus_runs`, never duplicated here.

`purpose`, `business_ref` and `input_version` are set by the server from a
checked business record. They are never accepted from a public request.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class NexusRun(TimestampMixin, Base):
    """Reference one NexusPilot run created on behalf of a local user."""

    __tablename__ = "nexus_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_nexus_runs_user_idempotency"
        ),
        Index("ix_nexus_runs_user_created", "user_id", "created_at"),
        Index("ix_nexus_runs_purpose_business", "purpose", "business_ref"),
        Index("ix_nexus_runs_user_purpose_created", "user_id", "purpose", "created_at"),
    )

    local_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    # Current NexusPilot resolves the application user from headers and does not
    # return a principal id. Older rows may retain one; new rows leave it NULL.
    nexus_principal_id: Mapped[str | None] = mapped_column(String(64))
    nexus_run_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    workflow_execution_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    last_event_sequence: Mapped[int] = mapped_column(default=0)
    request_text: Mapped[str] = mapped_column(String(4_000))
    result_json: Mapped[dict | None] = mapped_column(JSON)
    # Original validated input, so a retried idempotency key reuses the first
    # interpretation (including its "now") instead of rebuilding a different one.
    context_json: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Server-owned task metadata. Nullable only so pre-existing rows can be
    # classified by the migration instead of being guessed at write time.
    purpose: Mapped[str | None] = mapped_column(String(32))
    business_ref: Mapped[str | None] = mapped_column(String(36))
    input_version: Mapped[int | None] = mapped_column(Integer)
    trigger: Mapped[str | None] = mapped_column(String(16))


class UserAsk(TimestampMixin, Base):
    """One question the user submitted in chat, linked to its AI run.

    Nothing about execution state or the final answer is copied here: the chat
    list and detail read `nexus_runs` through `local_run_id`. A run without a
    row in this table is an internal task and is never listed as chat.
    """

    __tablename__ = "user_asks"
    __table_args__ = (Index("ix_user_asks_user_created", "user_id", "created_at"),)

    ask_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    local_run_id: Mapped[str] = mapped_column(
        ForeignKey("nexus_runs.local_run_id", ondelete="CASCADE"), unique=True
    )
    request_text: Mapped[str] = mapped_column(String(4_000))


class ToolCallReceipt(TimestampMixin, Base):
    """Remember one inbound tool call so a retried provider request cannot redo it.

    Storing the response is deliberate: a duplicate call must return the same
    bytes it returned the first time, including a bounded empty result.
    """

    __tablename__ = "tool_call_receipts"

    receipt_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tool_call_id: Mapped[str] = mapped_column(String(128), unique=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    external_user_id: Mapped[str] = mapped_column(String(128), index=True)
    # The current tool envelope carries tool_execution_id but no run id.
    nexus_run_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="completed")
    # Binds one execution id to the exact arguments it first executed. Legacy
    # rows are null and remain readable; new rows always carry the digest.
    arguments_sha256: Mapped[str | None] = mapped_column(String(64))
    response_json: Mapped[dict] = mapped_column(JSON)
