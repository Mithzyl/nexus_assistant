"""Small independent liveness signal for scheduler and worker processes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.models import CommuteCheck, NotificationOutbox, ServiceHeartbeat


@dataclass(frozen=True)
class ActiveJob:
    """The one claim a worker currently holds, renewed by the heartbeat loop."""

    kind: str  # "check" | "outbox"
    job_id: str
    owner: str


async def renew_job_lease(
    db: AsyncSession, job: ActiveJob, *, now: datetime, lease_seconds: int
) -> bool:
    """Extend only this owner's claim; a stolen/expired row is left untouched."""

    expires = now + timedelta(seconds=lease_seconds)
    if job.kind == "check":
        result = await db.execute(
            update(CommuteCheck)
            .where(
                CommuteCheck.check_id == job.job_id,
                CommuteCheck.status == "running",
                CommuteCheck.lease_owner == job.owner,
            )
            .values(lease_expires_at=expires)
            .execution_options(synchronize_session=False)
        )
    elif job.kind == "outbox":
        result = await db.execute(
            update(NotificationOutbox)
            .where(
                NotificationOutbox.outbox_id == job.job_id,
                NotificationOutbox.status == "sending",
                NotificationOutbox.lease_owner == job.owner,
            )
            .values(lease_expires_at=expires)
            .execution_options(synchronize_session=False)
        )
    else:  # pragma: no cover - programming error, never a remote input
        raise ValueError(f"unknown active job kind: {job.kind}")
    await db.commit()
    return result.rowcount == 1


async def record_heartbeat(
    db: AsyncSession,
    *,
    component: str,
    instance_id: str | None,
    current_check_id: str | None = None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    row = await db.get(ServiceHeartbeat, component)
    if row is None:
        db.add(ServiceHeartbeat(
            component=component, instance_id=instance_id,
            last_seen_at=now, current_check_id=current_check_id,
            created_at=now, updated_at=now,
        ))
    else:
        row.last_seen_at = now
        row.instance_id = instance_id
        row.current_check_id = current_check_id
        row.updated_at = now
    await db.commit()


async def heartbeat_loop(
    factory: async_sessionmaker[AsyncSession],
    *,
    component: str,
    instance_id: str,
    interval_seconds: float,
    stop: asyncio.Event,
    active_job: Callable[[], ActiveJob | None] | None = None,
    lease_seconds: int | None = None,
) -> None:
    """Run independently of a long check so the process stays visibly alive.

    Each tick also renews the worker's current claim. The loop is cancelled in
    the worker's ``finally`` block, so a long provider/AI call cannot make the
    process look offline or silently steal another executor's claim.
    """

    while not stop.is_set():
        job = active_job() if active_job is not None else None
        try:
            async with factory() as db:
                await record_heartbeat(
                    db,
                    component=component,
                    instance_id=instance_id,
                    current_check_id=job.job_id if job is not None and job.kind == "check" else None,
                )
                if job is not None and lease_seconds is not None:
                    await renew_job_lease(
                        db, job, now=datetime.now(UTC),
                        lease_seconds=lease_seconds,
                    )
        except Exception:  # noqa: BLE001 - heartbeat must never stop the worker
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def runtime_snapshot(
    db: AsyncSession, *, user_id: str, now: datetime, stale_seconds: int
) -> dict:
    components: dict[str, dict] = {}
    for name in ("scheduler", "worker"):
        row = await db.get(ServiceHeartbeat, name)
        if row is None or row.last_seen_at is None:
            components[name] = {"state": "not_seen", "last_seen_at": None}
            continue
        seen = row.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        state = "online" if now - seen <= timedelta(seconds=stale_seconds) else "stale"
        components[name] = {"state": state, "last_seen_at": seen}

    counts = {}
    for status in ("queued", "running"):
        counts[status] = await db.scalar(
            select(func.count()).select_from(CommuteCheck).where(
                CommuteCheck.user_id == user_id, CommuteCheck.status == status
            )
        ) or 0
    latest = await db.scalar(
        select(CommuteCheck).where(CommuteCheck.user_id == user_id)
        .order_by(CommuteCheck.created_at.desc()).limit(1)
    )
    return {
        "scheduler": components["scheduler"],
        "worker": components["worker"],
        "stale_after_seconds": stale_seconds,
        "queue": counts,
        "last_progress_at": (
            latest.phase_updated_at or latest.finished_at or latest.updated_at
        ) if latest else None,
        "latest_check": (
            {
                "check_id": latest.check_id,
                "status": latest.status,
                "phase": latest.phase,
                "phase_updated_at": latest.phase_updated_at,
            }
            if latest else None
        ),
    }
