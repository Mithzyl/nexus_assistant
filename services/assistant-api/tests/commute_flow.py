"""High-level helpers that exercise the real scheduler/worker service functions."""

from __future__ import annotations

from datetime import UTC, datetime

from assistant_api.commute.scheduler import scheduler_tick
from assistant_api.commute.services.checks import claim_check, execute_check
from assistant_api.commute.services.push import claim_outbox, deliver_outbox
from assistant_api.commute.timeutil import ensure_aware

from tests.commute_support import commute_plan_body


async def create_plan(harness, token: str, **overrides) -> dict:
    response = await harness.client.post(
        "/api/v1/commute-plans",
        json=commute_plan_body(**overrides),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def scheduler_run(harness, *, now: datetime | None = None) -> int:
    at = ensure_aware(now or harness.clock())
    async with harness.app.state.session_factory() as db:
        return await scheduler_tick(
            db, harness.settings, owner="test-scheduler", now=at
        )


async def execute_one_check(harness, *, now: datetime | None = None):
    at = ensure_aware(now or harness.clock())
    async with harness.app.state.session_factory() as db:
        check = await claim_check(
            db, worker_id="test-worker", now=at, lease_seconds=180
        )
        if check is None:
            return None
        return await execute_check(
            db, harness.runtime, check_id=check.check_id, worker_id="test-worker"
        )


async def deliver_one_outbox(harness, *, now: datetime | None = None):
    at = ensure_aware(now or harness.clock())
    async with harness.app.state.session_factory() as db:
        outbox = await claim_outbox(
            db, worker_id="test-pusher", now=at, lease_seconds=180
        )
        if outbox is None:
            return None
        return await deliver_outbox(
            db, harness.runtime, outbox_id=outbox.outbox_id, worker_id="test-pusher"
        )


async def run_full_auto_cycle(harness, *, now: datetime | None = None):
    at = ensure_aware(now or harness.clock())
    if now is not None and hasattr(harness.runtime.now, "set"):
        harness.runtime.now.set(at)
    await scheduler_run(harness, now=at)
    check = await execute_one_check(harness, now=at)
    outbox = await deliver_one_outbox(harness, now=at)
    return check, outbox
