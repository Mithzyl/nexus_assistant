"""Standalone commute worker: execute checks, then deliver outbox notifications.

Run with ``python -m assistant_api.commute.worker``. One logical reminder may be
sent to several devices, but it is one outbox row and one business idempotency
key, so the twice-a-day cap is not multiplied by device count.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from datetime import UTC, datetime
from uuid import uuid4

from assistant_api.commute.heartbeat import ActiveJob, heartbeat_loop, record_heartbeat
from assistant_api.commute.runtime import build_runtime, validate_commute_configuration
from assistant_api.commute.services.checks import claim_check, execute_check
from assistant_api.commute.services.push import (
    claim_outbox,
    deliver_outbox,
    expire_abandoned_deliveries,
)
from assistant_api.core.config import Settings, get_settings
from assistant_api.infrastructure.database import create_engine_and_factory
from assistant_api.models import Base

logger = logging.getLogger("commute.worker")


def worker_identity(prefix: str) -> str:
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"[:64]


async def worker_tick(
    db,
    runtime,
    *,
    worker_id: str,
    now: datetime | None = None,
    active_state: dict | None = None,
) -> tuple[bool, bool]:
    """Process at most one check and one outbox row.

    ``active_state`` is shared with the heartbeat loop so a long call keeps the
    process online and renews this worker's claim.
    """

    now = now or runtime.now()
    settings = runtime.settings
    check = await claim_check(
        db,
        worker_id=worker_id,
        now=now,
        lease_seconds=settings.commute_job_lease_seconds,
        max_attempts=settings.commute_provider_max_attempts,
    )
    did_check = check is not None
    if check is not None:
        check_id = check.check_id
        if active_state is not None:
            active_state["job"] = ActiveJob(kind="check", job_id=check.check_id, owner=worker_id)
        try:
            await asyncio.wait_for(
                execute_check(db, runtime, check_id=check.check_id, worker_id=worker_id),
                timeout=settings.commute_job_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - expired lease allows recovery
            await db.rollback()
            logger.exception("commute check %s failed", check_id)
        finally:
            if active_state is not None:
                active_state["job"] = None

    now = runtime.now()
    await expire_abandoned_deliveries(db, now=now)
    outbox = await claim_outbox(
        db,
        worker_id=worker_id,
        now=now,
        lease_seconds=settings.commute_job_lease_seconds,
    )
    did_push = outbox is not None
    if outbox is not None:
        outbox_id = outbox.outbox_id
        if active_state is not None:
            active_state["job"] = ActiveJob(kind="outbox", job_id=outbox.outbox_id, owner=worker_id)
        try:
            await asyncio.wait_for(deliver_outbox(
                db, runtime, outbox_id=outbox.outbox_id, worker_id=worker_id
            ), timeout=settings.commute_job_timeout_seconds)
        except Exception:  # noqa: BLE001 - lease expiry gives bounded recovery
            await db.rollback()
            logger.exception("commute outbox %s failed", outbox_id)
        finally:
            if active_state is not None:
                active_state["job"] = None
    return did_check, did_push


async def run_forever(settings: Settings, *, once: bool = False) -> None:
    validate_commute_configuration(settings)
    runtime = build_runtime(settings)
    engine, factory = create_engine_and_factory(settings.database_url)
    if settings.allow_schema_create:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    worker_id = worker_identity("worker")
    stop = asyncio.Event()
    active_state: dict = {"job": None}
    async with factory() as db:
        await record_heartbeat(db, component="worker", instance_id=worker_id)
    heartbeat = asyncio.create_task(heartbeat_loop(
        factory, component="worker", instance_id=worker_id,
        interval_seconds=settings.commute_heartbeat_interval_seconds, stop=stop,
        active_job=lambda: active_state.get("job"),
        lease_seconds=settings.commute_job_lease_seconds,
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
            async with factory() as db:
                did_check, did_push = await worker_tick(
                    db, runtime, worker_id=worker_id, active_state=active_state
                )
            if once:
                break
            if not did_check and not did_push:
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.commute_worker_poll_seconds
                    )
                except TimeoutError:
                    pass
    finally:
        stop.set()
        await heartbeat
        await runtime.aclose()
        await engine.dispose()
        logger.info("worker stopped")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one check/outbox and exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    asyncio.run(run_forever(get_settings(), once=args.once))


if __name__ == "__main__":
    main()
