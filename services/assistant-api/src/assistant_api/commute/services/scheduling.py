"""Instance creation and the automatic check cadence.

Runs only from the standalone scheduler process. Every operation is idempotent at
the database level, so a restarted or duplicated scheduler cannot create two
checks for the same plan/date/slot/revision.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.timeutil import (
    iso_weekday,
    local_today,
    slot_index_for,
    target_arrival_for,
    check_window_for,
)
from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommuteInstance,
    CommutePlan,
    NotificationOutbox,
)


async def get_instance(
    db: AsyncSession, *, plan_id: str, local_service_date
) -> CommuteInstance | None:
    return await db.scalar(
        select(CommuteInstance).where(
            CommuteInstance.plan_id == plan_id,
            CommuteInstance.local_service_date == local_service_date,
        )
    )


async def ensure_instance(
    db: AsyncSession,
    plan: CommutePlan,
    *,
    now: datetime,
    allow_non_scheduled_day: bool = False,
) -> CommuteInstance | None:
    """Create or reconcile today's instance for one plan.

    A past target arrival never creates a historical instance, so a restart does
    not backfill old notifications. Plan edits update the same row and therefore
    keep the twice-a-day cap across revisions.
    """

    if not plan.enabled:
        return None
    day = local_today(now, plan.timezone)
    if not allow_non_scheduled_day and iso_weekday(day) not in (plan.weekdays_json or []):
        return None
    target = target_arrival_for(day, plan.arrival_local_time, plan.timezone)
    window = check_window_for(day, plan.check_local_time, plan.timezone)
    existing = await get_instance(db, plan_id=plan.plan_id, local_service_date=day)
    if existing is None:
        if target <= now:
            return None
        instance = CommuteInstance(
            user_id=plan.user_id,
            plan_id=plan.plan_id,
            plan_revision=plan.revision,
            local_service_date=day,
            timezone=plan.timezone,
            target_arrival_at=target,
            check_window_start_at=window,
            status="active",
        )
        try:
            async with db.begin_nested():
                db.add(instance)
                await db.flush()
        except IntegrityError:
            existing = await get_instance(
                db, plan_id=plan.plan_id, local_service_date=day
            )
            if existing is None:
                return None
            instance = existing
        return instance

    changed = False
    if existing.plan_revision != plan.revision:
        existing.plan_revision = plan.revision
        changed = True
    if existing.timezone != plan.timezone:
        existing.timezone = plan.timezone
        changed = True
    if existing.target_arrival_at != target or existing.check_window_start_at != window:
        existing.target_arrival_at = target
        existing.check_window_start_at = window
        changed = True
    if existing.status in {"closed", "expired"} and target > now:
        existing.status = "active"
        changed = True
    if existing.status == "active" and target <= now:
        existing.status = "expired"
        changed = True
    if changed:
        await db.flush()
    return existing


async def latest_advisory(
    db: AsyncSession, *, user_id: str, plan_id: str, local_service_date=None
) -> CommuteAdvisory | None:
    query = select(CommuteAdvisory).where(
        CommuteAdvisory.user_id == user_id,
        CommuteAdvisory.plan_id == plan_id,
    )
    if local_service_date is not None:
        query = query.where(CommuteAdvisory.local_service_date == local_service_date)
    return await db.scalar(
        query
        .order_by(CommuteAdvisory.generated_at.desc(), CommuteAdvisory.created_at.desc())
        .limit(1)
    )


async def enqueue_due_checks(
    db: AsyncSession,
    *,
    now: datetime,
    interval_minutes: int = 10,
    analysis_mode: str = "full",
) -> list[str]:
    """Create one due auto check per plan at most every `interval_minutes`."""

    plans = list(
        (
            await db.scalars(
                select(CommutePlan)
                .where(CommutePlan.enabled.is_(True))
                .order_by(CommutePlan.updated_at)
            )
        ).all()
    )
    created: list[str] = []
    for plan in plans:
        instance = await ensure_instance(db, plan, now=now)
        if instance is None or instance.status != "active":
            continue
        if instance.status == "skipped":
            continue
        if instance.plan_revision != plan.revision:
            continue
        target = instance.target_arrival_at
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        if now >= target:
            instance.status = "expired"
            continue
        window = instance.check_window_start_at
        if window.tzinfo is None:
            window = window.replace(tzinfo=UTC)
        if now < window:
            continue

        advisory = await latest_advisory(
            db,
            user_id=plan.user_id,
            plan_id=plan.plan_id,
            local_service_date=instance.local_service_date,
        )
        if advisory is not None and advisory.recommended_departure_at is not None:
            departure = advisory.recommended_departure_at
            if departure.tzinfo is None:
                departure = departure.replace(tzinfo=UTC)
            if now >= departure:
                instance.status = "closed"
                await cancel_pending_checks(
                    db,
                    instance_id=instance.instance_id,
                    code="departure_passed",
                    now=now,
                )
                continue

        pending = await db.scalar(
            select(CommuteCheck.check_id).where(
                CommuteCheck.instance_id == instance.instance_id,
                CommuteCheck.trigger == "auto",
                CommuteCheck.status.in_(("queued", "running")),
            )
        )
        if pending is not None:
            continue

        recent = await db.scalar(
            select(CommuteCheck.created_at)
            .where(
                CommuteCheck.instance_id == instance.instance_id,
                CommuteCheck.trigger == "auto",
            )
            .order_by(CommuteCheck.created_at.desc())
            .limit(1)
        )
        if recent is not None:
            if recent.tzinfo is None:
                recent = recent.replace(tzinfo=UTC)
            if now - recent < timedelta(minutes=interval_minutes):
                continue

        slot = slot_index_for(now, plan.timezone, interval_minutes)
        check = CommuteCheck(
            user_id=plan.user_id,
            plan_id=plan.plan_id,
            instance_id=instance.instance_id,
            plan_revision=plan.revision,
            trigger="auto",
            analysis_mode=analysis_mode,
            phase="queued",
            phase_updated_at=now,
            slot_index=slot,
            status="queued",
            next_attempt_at=None,
            created_at=now,
            updated_at=now,
        )
        try:
            async with db.begin_nested():
                db.add(check)
                await db.flush()
        except IntegrityError:
            # Same slot or same plan-date-revision already enqueued.
            continue
        created.append(check.check_id)
    await db.commit()
    return created


async def close_instance(
    db: AsyncSession, *, instance_id: str, status: str = "closed"
) -> None:
    await db.execute(
        update(CommuteInstance)
        .where(CommuteInstance.instance_id == instance_id)
        .values(status=status)
    )


async def cancel_pending_checks(
    db: AsyncSession, *, instance_id: str, code: str, now: datetime
) -> None:
    await db.execute(
        update(CommuteCheck)
        .where(
            CommuteCheck.instance_id == instance_id,
            CommuteCheck.status == "queued",
        )
        .values(
            status="cancelled",
            phase="cancelled",
            phase_updated_at=now,
            next_attempt_at=None,
            error_code=code,
            error_message="任务已取消。",
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.instance_id == instance_id,
            NotificationOutbox.status == "pending",
        )
        .values(status="cancelled", last_error_code=code)
    )
