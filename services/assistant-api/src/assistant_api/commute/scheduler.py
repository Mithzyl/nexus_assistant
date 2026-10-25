"""Standalone scheduler entry point.

Run with ``python -m assistant_api.commute.scheduler``. It never lives inside the
API process, and a database lease prevents two scheduler processes from both
enqueuing for long. Duplicate work remains harmless thanks to unique slots.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.commute.runtime import build_runtime, validate_commute_configuration
from assistant_api.commute.heartbeat import heartbeat_loop, record_heartbeat
from assistant_api.commute.services.leases import acquire_lease
from assistant_api.commute.services.scheduling import enqueue_due_checks
from assistant_api.core.config import Settings, get_settings
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import Base, CommuteInstance, NotificationOutbox

logger = logging.getLogger("commute.scheduler")
LEASE_NAME = "commute-scheduler"


def worker_identity(prefix: str) -> str:
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"[:64]


def scheduled_analysis_mode(settings: Settings, runtime) -> str:
    """Paid analysis only when explicitly enabled and a route basis exists."""

    if settings.commute_auto_ai_enabled and getattr(runtime.eta, "available", False):
        return "full"
    return "weather_only"


async def scheduler_tick(
    db: AsyncSession,
    settings: Settings,
    *,
    owner: str,
    now: datetime | None = None,
    auto_analysis_mode: str = "full",
    enqueue_enabled: bool = True,
) -> int:
    now = now or datetime.now(UTC)
    acquired = await acquire_lease(
        db,
        name=LEASE_NAME,
        owner=owner,
        now=now,
        ttl_seconds=settings.commute_lease_seconds,
    )
    if not acquired:
        return 0
    # Instances past their target arrival are closed, never backfilled.
    await db.execute(
        update(CommuteInstance)
        .where(
            CommuteInstance.status == "active",
            CommuteInstance.target_arrival_at <= now,
        )
        .values(status="expired")
    )
    await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.status.in_(("pending", "sending")),
            NotificationOutbox.expires_at <= now,
        )
        .values(status="expired", last_error_code="notification_expired")
    )
    # Keep creating checks when the route provider is absent: weather refresh
    # and client-snapshot/manual paths must continue. The worker skips the
    # server route call and does not invoke the model without a route.
    created = []
    if enqueue_enabled:
        created = await enqueue_due_checks(
            db,
            now=now,
            interval_minutes=settings.commute_check_interval_minutes,
            analysis_mode=auto_analysis_mode,
        )
    if created:
        logger.info("enqueued %s commute checks", len(created))
    return len(created)


async def run_forever(settings: Settings) -> None:
    validate_commute_configuration(settings)
    runtime = build_runtime(settings)
    engine, factory = create_engine_and_factory(settings.database_url)
    if settings.allow_schema_create:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    owner = worker_identity("scheduler")
    stop = asyncio.Event()
    async with factory() as db:
        await record_heartbeat(db, component="scheduler", instance_id=owner)
    heartbeat = asyncio.create_task(heartbeat_loop(
        factory, component="scheduler", instance_id=owner,
        interval_seconds=settings.commute_heartbeat_interval_seconds, stop=stop,
    ))

    def _stop(*_args) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    try:
        while not stop.is_set():
            try:
                async with factory() as db:
                    await scheduler_tick(
                        db, settings, owner=owner,
                        auto_analysis_mode=scheduled_analysis_mode(settings, runtime),
                        enqueue_enabled=(
                            settings.commute_auto_weather_enabled
                            or settings.commute_auto_ai_enabled
                        ),
                    )
            except Exception:  # noqa: BLE001 - keep the loop alive, log no PII
                logger.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.commute_scheduler_poll_seconds
                )
            except TimeoutError:
                pass
    finally:
        stop.set()
        await heartbeat
        await runtime.aclose()
        await engine.dispose()
        logger.info("scheduler stopped")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    if args.once:
        asyncio.run(_run_once(get_settings()))
    else:
        asyncio.run(run_forever(get_settings()))


async def _run_once(settings: Settings) -> None:
    validate_commute_configuration(settings)
    runtime = build_runtime(settings)
    engine, factory = create_engine_and_factory(settings.database_url)
    if settings.allow_schema_create:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            count = await scheduler_tick(
                db, settings, owner=worker_identity("once"),
                auto_analysis_mode=scheduled_analysis_mode(settings, runtime),
                enqueue_enabled=(
                    settings.commute_auto_weather_enabled
                    or settings.commute_auto_ai_enabled
                ),
            )
        print(f"enqueued={count}")
    finally:
        await runtime.aclose()
        await engine.dispose()


if __name__ == "__main__":
    main()
