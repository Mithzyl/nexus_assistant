"""Progress, lease qualification and recovery contract for background runtime."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from assistant_api.commute.heartbeat import ActiveJob, renew_job_lease
from assistant_api.commute.services.checks import claim_check, execute_check
from assistant_api.models import CommuteAdvisory, CommuteCheck
from tests.commute_flow import create_plan, execute_one_check


def headers(token: str, extra: dict | None = None) -> dict:
    values = {"Authorization": f"Bearer {token}"}
    values.update(extra or {})
    return values


@pytest.mark.asyncio
async def test_stale_executor_cannot_persist_an_advisory(commute_harness, monkeypatch):
    """Only the current lease holder may commit results after a long call."""

    h = commute_harness
    plan = await create_plan(h, h.token_a)
    created = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "contract-stale-lease"}),
    )
    assert created.status_code == 202
    check_id = created.json()["check_id"]
    async with h.app.state.session_factory() as db:
        claimed = await claim_check(
            db, worker_id="old-worker", now=h.clock(), lease_seconds=180
        )
        assert claimed is not None and claimed.check_id == check_id

    original_fetch = h.runtime.weather.fetch

    async def steal_lease_then_fetch(**kwargs):
        async with h.app.state.session_factory() as other:
            await other.execute(
                update(CommuteCheck)
                .where(CommuteCheck.check_id == check_id)
                .values(
                    lease_owner="new-worker",
                    lease_expires_at=h.clock() + timedelta(seconds=180),
                )
                .execution_options(synchronize_session=False)
            )
            await other.commit()
        return await original_fetch(**kwargs)

    monkeypatch.setattr(h.runtime.weather, "fetch", steal_lease_then_fetch)
    async with h.app.state.session_factory() as db:
        result = await execute_check(
            db, h.runtime, check_id=check_id, worker_id="old-worker"
        )
    assert result is not None
    assert result.lease_owner == "new-worker"

    async with h.app.state.session_factory() as db:
        advisory = await db.scalar(
            select(CommuteAdvisory).where(CommuteAdvisory.check_id == check_id)
        )
        assert advisory is None
        row = await db.get(CommuteCheck, check_id)
        assert row.lease_owner == "new-worker"
        assert row.phase != "done"


@pytest.mark.asyncio
async def test_heartbeat_renews_only_the_current_owner(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    created = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "contract-lease-renew"}),
    )
    assert created.status_code == 202
    check_id = created.json()["check_id"]
    async with h.app.state.session_factory() as db:
        claimed = await claim_check(
            db, worker_id="worker-a", now=h.clock(), lease_seconds=180
        )
        assert claimed is not None and claimed.check_id == check_id
        before = claimed.lease_expires_at

    job = ActiveJob(kind="check", job_id=check_id, owner="worker-a")
    async with h.app.state.session_factory() as db:
        assert await renew_job_lease(
            db, job, now=h.clock() + timedelta(seconds=30), lease_seconds=180
        )
    async with h.app.state.session_factory() as db:
        refreshed = await db.get(CommuteCheck, check_id)
        assert refreshed.lease_expires_at > before

    wrong = ActiveJob(kind="check", job_id=check_id, owner="worker-b")
    async with h.app.state.session_factory() as db:
        assert not await renew_job_lease(
            db, wrong, now=h.clock() + timedelta(seconds=60), lease_seconds=180
        )


@pytest.mark.asyncio
async def test_completed_and_weather_only_checks_have_no_fake_retry_time(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    response = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "contract-no-fake-retry"}),
    )
    assert response.status_code == 202
    check = await execute_one_check(h)
    assert check.status == "succeeded"
    assert check.next_attempt_at is None
    assert check.phase == "done"
    payload = (await h.client.get(
        f"/api/v1/checks/{check.check_id}", headers=headers(h.token_a)
    )).json()
    assert payload["next_attempt_at"] is None
    assert payload["phase"] == "done"
    assert payload["analysis_mode"] == "full"
