from datetime import timedelta
import pytest
from assistant_api.commute.services.checks import claim_check
from assistant_api.commute.heartbeat import ActiveJob, renew_job_lease
from assistant_api.models import CommuteCheck
from tests.commute_flow import create_plan
from tests.test_commute_api import headers

@pytest.mark.asyncio
async def test_crash_budget_and_expired_renewal(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    response = await h.client.post(f"/api/v1/commute-plans/{plan['id']}/checks", headers=headers(h.token_a, {'Idempotency-Key': 'crash-budget'}))
    check_id = response.json()['check_id']
    async with h.app.state.session_factory() as db:
        for attempt in range(3):
            owner = f'worker-{attempt}'
            check = await claim_check(db, worker_id=owner, now=h.clock(), lease_seconds=30)
            assert check.check_id == check_id
            assert await claim_check(db, worker_id='competitor', now=h.clock(), lease_seconds=30) is None
            h.clock.advance(seconds=31)
            assert not await renew_job_lease(db, ActiveJob('check', check_id, owner), now=h.clock(), lease_seconds=30)
        assert await claim_check(db, worker_id='fourth', now=h.clock(), lease_seconds=30) is None
        await db.refresh(check)
        assert check.status == 'failed'
        assert check.error_code == 'execution_attempts_exhausted'
        assert check.attempt_count == 3

@pytest.mark.asyncio
async def test_timeout_stops_renewal_and_allows_recovery(commute_harness, monkeypatch):
    import asyncio
    from assistant_api.commute import worker
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    response = await h.client.post(f"/api/v1/commute-plans/{plan['id']}/checks", headers=headers(h.token_a, {'Idempotency-Key': 'timeout-recovery'}))
    assert response.status_code == 202
    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(worker, 'execute_check', blocked)
    h.runtime.settings.commute_job_timeout_seconds = .01
    h.clock.advance(seconds=1)
    state = {}
    async with h.app.state.session_factory() as db:
        await worker.worker_tick(db, h.runtime, worker_id='timed-out', now=h.clock(), active_state=state)
        assert state.get('job', 'missing') is None, (h.runtime.settings.commute_provider_max_attempts, (await db.get(CommuteCheck, response.json()['check_id'])).status)
        h.clock.advance(seconds=h.runtime.settings.commute_job_lease_seconds + 1)
        recovered = await claim_check(db, worker_id='replacement', now=h.clock(), lease_seconds=30)
        assert recovered.check_id == response.json()['check_id']
        assert recovered.attempt_count == 2
