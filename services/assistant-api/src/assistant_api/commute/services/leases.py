"""Database leases that keep duplicated processes from repeating work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import CommuteLease


async def acquire_lease(
    db: AsyncSession,
    *,
    name: str,
    owner: str,
    now: datetime,
    ttl_seconds: int,
) -> bool:
    """Insert or steal an expired lease atomically.

    The scheduler's work is idempotent regardless; the lease avoids most duplicate
    vendor calls when two processes are accidentally started.
    """

    row = await db.scalar(select(CommuteLease).where(CommuteLease.lease_name == name))
    expires = now + timedelta(seconds=ttl_seconds)
    if row is None:
        try:
            async with db.begin_nested():
                db.add(CommuteLease(lease_name=name, owner=owner, expires_at=expires))
                await db.flush()
            await db.commit()
            return True
        except IntegrityError:
            row = await db.scalar(
                select(CommuteLease).where(CommuteLease.lease_name == name)
            )
            if row is None:
                await db.rollback()
                return False
    if row.owner == owner and now <= ensure_aware(row.expires_at):
        await db.execute(
            update(CommuteLease)
            .where(CommuteLease.lease_name == name, CommuteLease.owner == owner)
            .values(expires_at=expires)
        )
        await db.commit()
        return True
    if ensure_aware(row.expires_at) <= now:
        result = await db.execute(
            update(CommuteLease)
            .where(
                CommuteLease.lease_name == name,
                CommuteLease.expires_at == row.expires_at,
            )
            .values(owner=owner, expires_at=expires)
        )
        await db.commit()
        return result.rowcount == 1
    await db.rollback()
    return False


async def release_lease(db: AsyncSession, *, name: str, owner: str) -> None:
    await db.execute(
        update(CommuteLease)
        .where(CommuteLease.lease_name == name, CommuteLease.owner == owner)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
