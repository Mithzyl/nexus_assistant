"""Durable state for the server-side commute reminder module.

Every row is owned by one local user. Location coordinates live only on the plan;
provider summaries and push attempts deliberately keep no coordinates, labels or
APNs tokens (only a digest), so debugging and retention cannot leak a home address.
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class CommutePlan(TimestampMixin, Base):
    """A fixed recurring commute the user configured; the server owns computation."""

    __tablename__ = "commute_plans"
    __table_args__ = (Index("ix_commute_plans_user_enabled", "user_id", "enabled"),)

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String(64))
    weekdays_json: Mapped[list] = mapped_column(JSON, default=list)
    arrival_local_time: Mapped[str] = mapped_column(String(5))
    check_local_time: Mapped[str] = mapped_column(String(5))
    transport_mode: Mapped[str] = mapped_column(String(16))
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=10)
    rain_probability_threshold: Mapped[float] = mapped_column(Float, default=0.4)
    rain_extra_buffer_minutes: Mapped[int] = mapped_column(Integer, default=10)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    origin_label: Mapped[str] = mapped_column(String(160))
    origin_latitude: Mapped[float] = mapped_column(Float)
    origin_longitude: Mapped[float] = mapped_column(Float)
    destination_label: Mapped[str] = mapped_column(String(160))
    destination_latitude: Mapped[float] = mapped_column(Float)
    destination_longitude: Mapped[float] = mapped_column(Float)

    effective_from: Mapped[date | None] = mapped_column(Date)
    matter_id: Mapped[str | None] = mapped_column(ForeignKey("matters.matter_id"))
    first_schedule_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.schedule_id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class CommuteInstance(TimestampMixin, Base):
    """One (plan, local service date) execution instance.

    The unique key survives plan edits, which is what keeps the twice-a-day
    reminder cap working across plan revisions.
    """

    __tablename__ = "commute_instances"
    __table_args__ = (
        UniqueConstraint("plan_id", "local_service_date", name="uq_commute_instance_day"),
        Index("ix_commute_instances_user_date", "user_id", "local_service_date"),
        Index("ix_commute_instances_status", "status", "target_arrival_at"),
    )

    instance_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("commute_plans.plan_id", ondelete="CASCADE"), index=True
    )
    plan_revision: Mapped[int] = mapped_column(Integer)
    local_service_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    target_arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    check_window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")
    auto_reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_auto_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skip_today_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_advisory_id: Mapped[str | None] = mapped_column(String(36))


class CommuteCheck(TimestampMixin, Base):
    """A queued/executed refresh. Checks and notifications are separate records."""

    __tablename__ = "commute_checks"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "plan_revision",
            "slot_index",
            name="uq_commute_check_slot",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_commute_check_idem"),
        Index("ix_commute_checks_claim", "status", "next_attempt_at"),
        Index("ix_commute_checks_instance", "instance_id", "created_at"),
    )

    check_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("commute_plans.plan_id", ondelete="CASCADE"), index=True
    )
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("commute_instances.instance_id", ondelete="CASCADE")
    )
    plan_revision: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16))
    analysis_mode: Mapped[str | None] = mapped_column(String(16))
    phase: Mapped[str | None] = mapped_column(String(32))
    phase_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slot_index: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    advisory_id: Mapped[str | None] = mapped_column(String(36))
    advisory_created: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deprecated placeholders, never written. The authoritative link is the
    # snapshot side: `commute_weather_snapshots.check_id` (unique) and
    # `commute_eta_snapshots.check_id`. Readers must use that direction only, so
    # the two possible links cannot disagree.
    weather_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    eta_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    route_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    route_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    ai_run_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(255))


class CommuteWeatherSnapshot(TimestampMixin, Base):
    """Provider summary for one check; intentionally has no coordinates."""

    __tablename__ = "commute_weather_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    check_id: Mapped[str] = mapped_column(
        ForeignKey("commute_checks.check_id", ondelete="CASCADE"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(16))
    freshness: Mapped[str] = mapped_column(String(16))
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)


class CommuteEtaSnapshot(TimestampMixin, Base):
    """ETA fact plus the future time basis the request used."""

    __tablename__ = "commute_eta_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    check_id: Mapped[str] = mapped_column(
        ForeignKey("commute_checks.check_id", ondelete="CASCADE"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    transport_mode: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_depart_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    distance_meters: Mapped[int | None] = mapped_column(Integer)
    freshness: Mapped[str] = mapped_column(String(16))
    response_basis: Mapped[str] = mapped_column(String(64))
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)


class CommuteAdvisory(TimestampMixin, Base):
    """One computed recommendation. Notification is a separate outbox row."""

    __tablename__ = "commute_advisories"
    __table_args__ = (
        UniqueConstraint("check_id", name="uq_commute_advisory_check"),
        Index("ix_commute_advisories_plan_time", "plan_id", "generated_at"),
        Index("ix_commute_advisories_user_time", "user_id", "generated_at"),
    )

    advisory_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("commute_plans.plan_id", ondelete="CASCADE"), index=True
    )
    check_id: Mapped[str] = mapped_column(String(36))
    plan_revision: Mapped[int] = mapped_column(Integer)
    local_service_date: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    target_arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recommended_departure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    departure_status: Mapped[str] = mapped_column(String(16))
    eta_seconds: Mapped[int | None] = mapped_column(Integer)
    buffer_seconds: Mapped[int] = mapped_column(Integer)
    weather_extra_buffer_seconds: Mapped[int] = mapped_column(Integer, default=0)
    weather_json: Mapped[dict | None] = mapped_column(JSON)
    sources_json: Mapped[list] = mapped_column(JSON, default=list)
    reason_codes_json: Mapped[list] = mapped_column(JSON, default=list)
    explanation_source: Mapped[str] = mapped_column(String(16), default="template")
    ai_analysis_json: Mapped[dict | None] = mapped_column(JSON)


class PushDevice(TimestampMixin, Base):
    """A current user's APNs binding for one app installation.

    `installation_id` identifies an install, not a person. The server chooses the
    APNs topic and environment; neither is accepted from the client.
    """

    __tablename__ = "push_devices"
    __table_args__ = (
        UniqueConstraint("user_id", "installation_id", name="uq_push_device_install"),
        Index("ix_push_devices_token", "apns_token"),
    )

    device_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_sessions.session_id", ondelete="SET NULL")
    )
    installation_id: Mapped[str] = mapped_column(String(128))
    apns_token: Mapped[str] = mapped_column(String(256))
    notification_permission: Mapped[str] = mapped_column(String(24))
    environment: Mapped[str] = mapped_column(String(16))
    topic: Mapped[str] = mapped_column(String(255))
    token_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationOutbox(TimestampMixin, Base):
    """Transactional outbox for one logical reminder.

    `(plan_id, local_service_date, reminder_sequence)` is the business idempotency
    key. `collapse_id` is only a display-collapsing hint and never replaces it.
    """

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "local_service_date",
            "reminder_sequence",
            name="uq_outbox_plan_day_sequence",
        ),
        UniqueConstraint("advisory_id", name="uq_outbox_advisory"),
        Index("ix_outbox_claim", "status", "next_attempt_at"),
        Index("ix_outbox_expiry", "status", "expires_at"),
    )

    outbox_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("commute_plans.plan_id", ondelete="CASCADE"), index=True
    )
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("commute_instances.instance_id", ondelete="CASCADE")
    )
    advisory_id: Mapped[str] = mapped_column(
        ForeignKey("commute_advisories.advisory_id", ondelete="CASCADE"), index=True
    )
    local_service_date: Mapped[date] = mapped_column(Date)
    reminder_sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    payload_json: Mapped[dict] = mapped_column(JSON)
    collapse_id: Mapped[str] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class PushAttempt(TimestampMixin, Base):
    """Per-device result. The APNs token is represented by a digest only."""

    __tablename__ = "push_attempts"
    __table_args__ = (Index("ix_push_attempts_outbox", "outbox_id", "created_at"),)

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    outbox_id: Mapped[str] = mapped_column(
        ForeignKey("notification_outbox.outbox_id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str | None] = mapped_column(String(36))
    token_digest: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    result: Mapped[str] = mapped_column(String(24))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdvisoryAction(TimestampMixin, Base):
    """Audit of an App-reported action; `opened` is not proof of delivery."""

    __tablename__ = "commute_advisory_actions"
    __table_args__ = (
        UniqueConstraint(
            "advisory_id", "action", "installation_id", name="uq_advisory_action_install"
        ),
    )

    action_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    advisory_id: Mapped[str] = mapped_column(
        ForeignKey("commute_advisories.advisory_id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str] = mapped_column(String(36))
    instance_id: Mapped[str | None] = mapped_column(String(36))
    installation_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(24))


class CommuteIdempotencyKey(TimestampMixin, Base):
    """User- and route-scoped replay record for mutating requests."""

    __tablename__ = "commute_idempotency_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "scope", "key", name="uq_commute_idempotency"),
    )

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(36))
    response_json: Mapped[dict] = mapped_column(JSON)


class CommuteLease(TimestampMixin, Base):
    """Database lease used to ensure only one scheduler/worker performs a job."""

    __tablename__ = "commute_leases"

    lease_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServiceHeartbeat(TimestampMixin, Base):
    """Last successful poll of one background role; no secrets or user data."""

    __tablename__ = "service_heartbeats"

    component: Mapped[str] = mapped_column(String(32), primary_key=True)
    instance_id: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_check_id: Mapped[str | None] = mapped_column(String(36))
