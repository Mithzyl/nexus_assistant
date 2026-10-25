"""Operational behaviour the clients depend on.

Covers three separate signals the runtime endpoint must not conflate (the server
answering, a background process reporting in, and a task advancing), the honest
"queued but not started" state when no worker runs, and the fact that reading
weather or an advisory is a pure read.
"""

from __future__ import annotations

import pytest

from assistant_api.models import CommuteAdvisory, CommuteCheck
from conftest import auth

from tests.commute_flow import create_plan, execute_one_check


def headers(token: str, extra: dict | None = None) -> dict:
    values = {"Authorization": f"Bearer {token}"}
    values.update(extra or {})
    return values


async def _create_check(harness, plan_id: str, key: str) -> str:
    response = await harness.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks",
        headers=headers(harness.token_a, {"Idempotency-Key": key}),
    )
    assert response.status_code == 202, response.text
    return response.json()["check_id"]


@pytest.mark.asyncio
async def test_task_waits_honestly_when_no_background_process_runs(commute_harness):
    """202 means accepted, not started: nothing may claim progress or a result."""

    h = commute_harness
    plan = await create_plan(h, h.token_a)
    check_id = await _create_check(h, plan["id"], "ops-wait-0001")

    detail = await h.client.get(
        f"/api/v1/checks/{check_id}", headers=headers(h.token_a)
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "queued"
    assert body["phase"] == "queued"
    assert body["advisory_id"] is None
    assert body["advisory_created"] is False
    assert body["next_attempt_at"] is None
    assert body["error"] is None

    runtime = await h.client.get(
        "/api/v1/commute/runtime", headers=headers(h.token_a)
    )
    assert runtime.status_code == 200
    live = runtime.json()
    assert live["server_time"].endswith("Z")
    assert live["worker"]["state"] == "not_seen"
    assert live["scheduler"]["state"] == "not_seen"
    assert live["queue"] == {"queued": 1, "running": 0}
    assert live["latest_check"]["check_id"] == check_id
    assert live["latest_check"]["status"] == "queued"
    assert "instance_id" not in live["latest_check"]

    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=headers(h.token_a),
    )
    assert latest.status_code == 404
    assert latest.json()["error"]["code"] == "NO_ADVISORY"

    weather = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=headers(h.token_a)
    )
    assert weather.status_code == 404
    assert weather.json()["error"]["code"] == "NO_WEATHER"

    # Only a worker execution advances the task.
    completed = await execute_one_check(h)
    assert completed is not None and completed.check_id == check_id
    assert completed.status in {"succeeded", "degraded"}

    after = await h.client.get(
        "/api/v1/commute/runtime", headers=headers(h.token_a)
    )
    assert after.json()["queue"] == {"queued": 0, "running": 0}
    done = await h.client.get(
        f"/api/v1/checks/{check_id}", headers=headers(h.token_a)
    )
    assert done.json()["phase"] == "done"


@pytest.mark.asyncio
async def test_weather_query_is_a_pure_read(commute_harness):
    """Reading weather must not call a model, create an advisory or move latest."""

    h = commute_harness
    plan = await create_plan(h, h.token_a)
    check_id = await _create_check(h, plan["id"], "ops-weather-0001")
    async with h.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        check.analysis_mode = "weather_only"
        await db.commit()
    await execute_one_check(h)

    calls_before = len(h.fake.calls)
    first = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=headers(h.token_a)
    )
    assert first.status_code == 200
    second = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=headers(h.token_a)
    )
    assert second.status_code == 200
    assert second.json()["check_id"] == first.json()["check_id"]
    assert second.json()["fetched_at"] == first.json()["fetched_at"]
    assert len(h.fake.calls) == calls_before, "weather reads must not call upstream"

    async with h.app.state.session_factory() as db:
        advisories = (
            await db.execute(
                CommuteAdvisory.__table__.select().where(
                    CommuteAdvisory.plan_id == plan["id"]
                )
            )
        ).all()
    assert advisories == []

    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=headers(h.token_a),
    )
    assert latest.status_code == 404


@pytest.mark.asyncio
async def test_advisory_is_read_through_the_check_identifier(commute_harness):
    """A client reads check -> advisory_id -> advisory, never a different latest."""

    h = commute_harness
    plan = await create_plan(h, h.token_a)
    check_id = await _create_check(h, plan["id"], "ops-advisory-0001")
    executed = await execute_one_check(h)
    assert executed is not None and executed.advisory_id

    check = await h.client.get(
        f"/api/v1/checks/{check_id}", headers=headers(h.token_a)
    )
    assert check.status_code == 200
    assert check.json()["advisory_id"] == executed.advisory_id
    assert check.json()["advisory_created"] is True

    advisory = await h.client.get(
        f"/api/v1/advisories/{executed.advisory_id}", headers=headers(h.token_a)
    )
    assert advisory.status_code == 200
    body = advisory.json()
    assert body["id"] == executed.advisory_id
    assert body["matches_current_plan"] is True
    assert body["weather"] is not None
    assert {source["kind"] for source in body["sources"]} == {"weather", "route"}

    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=headers(h.token_a),
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]

    other = await h.client.get(
        f"/api/v1/advisories/{executed.advisory_id}", headers=headers(h.token_b)
    )
    assert other.status_code == 404
