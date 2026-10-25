"""DST scheduling, stop conditions and restart semantics across real service calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from assistant_api.commute.services.checks import claim_check, execute_check
from assistant_api.models import CommuteCheck, CommuteInstance
from tests.commute_flow import create_plan, execute_one_check, scheduler_run
from tests.test_commute_worker_flow import auth, plan_edit_body


@pytest.mark.asyncio
async def test_spring_forward_gap_shifts_check_to_first_valid_instant(commute_harness):
    h = commute_harness
    # Sunday 2026-03-08 in New York: 02:30 does not exist.
    h.clock.set(datetime(2026, 3, 8, 6, 59, tzinfo=UTC))
    await create_plan(
        h,
        h.token_a,
        timezone="America/New_York",
        weekdays=[7],
        check_local_time="02:30",
        arrival_local_time="04:00",
    )
    assert await scheduler_run(h) == 0
    async with h.app.state.session_factory() as db:
        instance = await db.scalar(select(CommuteInstance))
        from assistant_api.commute.timeutil import ensure_aware

        assert ensure_aware(instance.check_window_start_at) == datetime(
            2026, 3, 8, 7, 0, tzinfo=UTC
        )

    h.clock.set(datetime(2026, 3, 8, 7, 0, tzinfo=UTC))
    assert await scheduler_run(h) == 1


@pytest.mark.asyncio
async def test_fall_back_repeated_local_time_executes_only_once(commute_harness):
    h = commute_harness
    h.clock.set(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))  # first 01:30 EDT
    await create_plan(
        h,
        h.token_a,
        timezone="America/New_York",
        weekdays=[7],
        check_local_time="01:00",
        arrival_local_time="03:00",
    )
    assert await scheduler_run(h) == 1
    check = await execute_one_check(h, now=datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    assert check is not None and check.advisory_id is not None

    # 06:30Z is the second 01:30 local occurrence. The local slot is shared, so
    # the unique check key refuses a duplicate.
    h.clock.set(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert await scheduler_run(h) == 0
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(CommuteCheck)) == 1


@pytest.mark.asyncio
async def test_auto_checks_stop_at_recommended_departure(commute_harness):
    h = commute_harness
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    first = await execute_one_check(h)
    assert first.advisory_id is not None
    h.clock.set(datetime(2026, 10, 23, 0, 20, tzinfo=UTC))
    assert await scheduler_run(h) == 0
    async with h.app.state.session_factory() as db:
        instance = await db.scalar(select(CommuteInstance))
        assert instance.status == "closed"


@pytest.mark.asyncio
async def test_restart_after_target_does_not_backfill_history(commute_harness):
    h = commute_harness
    h.clock.set(datetime(2026, 10, 23, 3, 0, tzinfo=UTC))  # 11:00 local, past 09:00
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 0
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(CommuteInstance)) == 0
        assert await db.scalar(select(func.count()).select_from(CommuteCheck)) == 0


@pytest.mark.asyncio
async def test_plan_edit_during_execution_cancels_the_old_revision(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    async with h.app.state.session_factory() as db:
        check = await claim_check(
            db, worker_id="slow-worker", now=h.clock(), lease_seconds=180
        )
        assert check is not None
        check_id = check.check_id

    edited = await h.client.put(
        f"/api/v1/commute-plans/{plan['id']}",
        json=plan_edit_body(plan, name="执行中修改"),
        headers=auth(h.token_a, {"If-Match": '"1"'}),
    )
    assert edited.status_code == 200

    async with h.app.state.session_factory() as db:
        result = await execute_check(
            db, h.runtime, check_id=check_id, worker_id="slow-worker"
        )
    assert result.status == "cancelled"
    assert result.error_code == "plan_revision_superseded"


@pytest.mark.asyncio
async def test_plan_deleted_before_execution_cancels_cleanly(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    async with h.app.state.session_factory() as db:
        check = await claim_check(
            db, worker_id="slow-worker", now=h.clock(), lease_seconds=180
        )
        check_id = check.check_id
    deleted = await h.client.delete(
        f"/api/v1/commute-plans/{plan['id']}", headers=auth(h.token_a)
    )
    assert deleted.status_code == 204
    async with h.app.state.session_factory() as db:
        result = await execute_check(
            db, h.runtime, check_id=check_id, worker_id="slow-worker"
        )
    assert result is None


@pytest.mark.asyncio
async def test_queued_check_past_target_is_cancelled(commute_harness):
    h = commute_harness
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    h.clock.set(datetime(2026, 10, 23, 1, 1, tzinfo=UTC))  # past 09:00 local
    assert await scheduler_run(h) == 0
    check = await execute_one_check(h)
    assert check is not None and check.status == "cancelled"
    assert check.error_code == "target_arrival_passed"


@pytest.mark.asyncio
async def test_unsent_outbox_expires_at_target_arrival(commute_harness):
    h = commute_harness
    from assistant_api.models import NotificationOutbox
    from sqlalchemy import select
    from tests.commute_flow import deliver_one_outbox
    from tests.test_commute_worker_flow import register_device

    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    check = await execute_one_check(h)
    assert check is not None
    async with h.app.state.session_factory() as db:
        outbox = await db.scalar(select(NotificationOutbox))
    assert outbox.status == "pending"

    h.clock.set(datetime(2026, 10, 23, 1, 1, tzinfo=UTC))
    assert await deliver_one_outbox(h) is None
    async with h.app.state.session_factory() as db:
        outbox = await db.scalar(select(NotificationOutbox))
    assert outbox.status == "expired"
    assert h.push.messages == []
