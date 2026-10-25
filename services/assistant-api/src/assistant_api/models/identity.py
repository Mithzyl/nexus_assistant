"""Local users, profiles, sessions, and the NexusPilot identity link.

`nexus_identity_links` is the only place where a local user is associated with an
execution principal. It is a cache of a decision NexusPilot owns: the row may be
discarded and rebuilt at any time, and a mismatch is resolved by asking
NexusPilot again rather than by trusting local data.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class User(TimestampMixin, Base):
    """Represent one Personal Assistant account, independent of NexusPilot login."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_verifier: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active")


class UserProfile(TimestampMixin, Base):
    """Store personalization the assistant needs before any model call."""

    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    locale: Mapped[str] = mapped_column(String(16), default="zh-CN")
    preferences_json: Mapped[dict] = mapped_column(JSON, default=dict)


class UserSession(TimestampMixin, Base):
    """Represent one revocable device session; only the token digest is stored."""

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_expires", "user_id", "expires_at"),)

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NexusIdentityLink(TimestampMixin, Base):
    """Cache the application-scoped mapping `external_user_id -> pilot principal`.

    The uniqueness constraint is what makes implicit provisioning safe: two
    concurrent first requests for the same local user cannot create two
    principals, because the second insert fails and is re-read.
    """

    __tablename__ = "nexus_identity_links"
    __table_args__ = (
        UniqueConstraint(
            "nexus_application_id",
            "external_user_id",
            name="uq_nexus_link_application_external_user",
        ),
    )

    link_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    nexus_application_id: Mapped[str] = mapped_column(String(64))
    external_user_id: Mapped[str] = mapped_column(String(128), index=True)
    nexus_principal_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
