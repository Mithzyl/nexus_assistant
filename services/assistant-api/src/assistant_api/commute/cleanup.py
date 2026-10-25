"""Retention cleanup for old commute execution history.

Plan deletion removes all child rows immediately (see services/plans.py). This
routine bounds history that remains after active plans keep running. It never
removes a pending/sending outbox row or an advisory that is still unexpired.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.core.config import get_settings
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import (
    AdvisoryAction,
    CommuteIdempotencyKey,
    CommuteAdvisory,
    CommuteCheck,
    CommuteEtaSnapshot,
    CommuteWeatherSnapshot,
    NotificationOutbox,
    PushAttempt,
)


async def cleanup(days: int) -> dict[str, int]:
    settings = get_settings()
    engine, factory = create_engine_and_factory(settings.database_url)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    counts: dict[str, int] = {}
    try:
        async with factory() as db:
            active_outbox_ids = set(
                (
                    await db.scalars(
                        select(NotificationOutbox.outbox_id).where(
                            NotificationOutbox.status.in_(("pending", "sending"))
                        )
                    )
                ).all()
            )
            old_advisories = list(
                (
                    await db.scalars(
                        select(CommuteAdvisory.advisory_id).where(
                            CommuteAdvisory.expires_at < cutoff
                        )
                    )
                ).all()
            )
            blocked = set(
                (
                    await db.scalars(
                        select(NotificationOutbox.advisory_id).where(
                            NotificationOutbox.outbox_id.in_(active_outbox_ids)
                        )
                    )
                ).all()
            )
            removable = [item for item in old_advisories if item not in blocked]
            if removable:
                await db.execute(
                    delete(AdvisoryAction).where(
                        AdvisoryAction.advisory_id.in_(removable)
                    )
                )
                outbox_ids = list(
                    (
                        await db.scalars(
                            select(NotificationOutbox.outbox_id).where(
                                NotificationOutbox.advisory_id.in_(removable)
                            )
                        )
                    ).all()
                )
                if outbox_ids:
                    await db.execute(
                        delete(PushAttempt).where(PushAttempt.outbox_id.in_(outbox_ids))
                    )
                    await db.execute(
                        delete(NotificationOutbox).where(
                            NotificationOutbox.outbox_id.in_(outbox_ids)
                        )
                    )
                counts["advisories"] = len(removable)
                await db.execute(
                    delete(CommuteAdvisory).where(
                        CommuteAdvisory.advisory_id.in_(removable)
                    )
                )

            old_checks = list(
                (
                    await db.scalars(
                        select(CommuteCheck.check_id).where(
                            CommuteCheck.created_at < cutoff,
                            CommuteCheck.status.in_(
                                ("succeeded", "degraded", "failed", "cancelled")
                            ),
                        )
                    )
                ).all()
            )
            if old_checks:
                await db.execute(
                    delete(CommuteWeatherSnapshot).where(
                        CommuteWeatherSnapshot.check_id.in_(old_checks)
                    )
                )
                await db.execute(
                    delete(CommuteEtaSnapshot).where(
                        CommuteEtaSnapshot.check_id.in_(old_checks)
                    )
                )
                counts["checks"] = len(old_checks)
                await db.execute(
                    delete(CommuteCheck).where(CommuteCheck.check_id.in_(old_checks))
                )
            result = await db.execute(
                delete(CommuteIdempotencyKey).where(
                    CommuteIdempotencyKey.created_at < cutoff
                )
            )
            counts["idempotency_keys"] = result.rowcount or 0
            await db.commit()
    finally:
        await engine.dispose()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()
    days = args.days or get_settings().commute_data_retention_days
    print(asyncio.run(cleanup(days)))


if __name__ == "__main__":
    main()
