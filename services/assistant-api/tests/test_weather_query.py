"""Weather query and advisory applicability, read-time only."""

from __future__ import annotations

import pytest

from assistant_api.models import CommuteAdvisory, CommuteCheck
from conftest import auth

from tests.commute_flow import create_plan, execute_one_check
from tests.test_ai_task_service import ai_answer, ai_harness, load_state, manual_check  # noqa: F401


async def weather_only_check(harness, plan_id: str, key: str) -> str:
    check_id = await manual_check(harness, plan_id, key)
    async with harness.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        check.analysis_mode = "weather_only"
        await db.commit()
    await execute_one_check(harness)
    return check_id


async def full_check(harness, plan_id: str, key: str):
    check_id = await manual_check(harness, plan_id, key)
    _check, _plan, instance, _ = await load_state(harness, check_id)
    harness.fake.final_text = ai_answer(instance.target_arrival_at)
    await execute_one_check(harness)
    check, _plan, _instance, _run = await load_state(harness, check_id)
    return check


@pytest.mark.asyncio
async def test_weather_only_snapshot_is_readable_for_the_plan(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    check_id = await weather_only_check(h, plan["id"], "weather-only-0001")

    response = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=auth(h.token_a)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan_id"] == plan["id"]
    assert body["plan_revision"] == plan["revision"]
    assert body["local_service_date"] == "2026-10-23"
    assert body["check_id"] == check_id
    assert body["provider"] == "fake_weather"
    assert body["quality"] == "fresh"
    assert body["coverage"] == "full"
    assert body["is_stale"] is False
    assert body["matches_current_plan"] is True
    assert body["current_plan_revision"] == plan["revision"]
    assert body["fetched_at"].endswith("Z")
    assert body["valid_from"] and body["valid_to"]
    assert body["summary"]["precipitation_probability_max"] == 0.2
    assert body["summary"]["rain_risk"] is False

    # weather_only never creates or replaces an advisory.
    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=auth(h.token_a),
    )
    assert latest.status_code == 404
    assert latest.json()["error"]["code"] == "NO_ADVISORY"


@pytest.mark.asyncio
async def test_weather_is_absent_rather_than_borrowed(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    missing = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=auth(h.token_a)
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NO_WEATHER"

    other_day = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather",
        params={"local_service_date": "2026-10-22"},
        headers=auth(h.token_a),
    )
    assert other_day.status_code == 404

    await weather_only_check(h, plan["id"], "weather-only-0002")
    other_account = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=auth(h.token_b)
    )
    assert other_account.status_code == 404


@pytest.mark.asyncio
async def test_newer_weather_does_not_rewrite_an_older_advisory(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    check = await full_check(h, plan["id"], "weather-advisory-0001")
    assert check.advisory_id

    first = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=auth(h.token_a),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["weather"]["precipitation_probability_max"] == 0.2
    assert first_body["matches_current_plan"] is True
    assert first_body["is_current_service_date"] is True
    assert first_body["is_expired"] is False

    h.weather.probability = 0.9
    await weather_only_check(h, plan["id"], "weather-advisory-0002")

    fresh_weather = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=auth(h.token_a)
    )
    assert fresh_weather.status_code == 200
    assert fresh_weather.json()["summary"]["precipitation_probability_max"] == 0.9

    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=auth(h.token_a),
    )
    assert latest.status_code == 200
    latest_body = latest.json()
    assert latest_body["id"] == first_body["id"]
    assert latest_body["weather"]["precipitation_probability_max"] == 0.2
    assert latest_body["generated_at"] == first_body["generated_at"]
    assert {
        source["kind"] for source in latest_body["sources"]
    } == {"weather", "route"}


@pytest.mark.asyncio
async def test_plan_change_marks_weather_and_advisory_as_not_current(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    await full_check(h, plan["id"], "weather-revision-0001")
    await weather_only_check(h, plan["id"], "weather-revision-0002")

    updated = await h.client.put(
        f"/api/v1/commute-plans/{plan['id']}",
        json={
            "name": "上班通勤",
            "enabled": True,
            "timezone": "Asia/Shanghai",
            "weekdays": [1, 2, 3, 4, 5],
            "arrival_local_time": "09:30",
            "check_local_time": "07:30",
            "transport_mode": "driving",
            "buffer_minutes": 10,
            "rain_probability_threshold": 0.4,
            "rain_extra_buffer_minutes": 10,
            "notify_enabled": True,
            "origin": {"label": "家", "latitude": 31.2304, "longitude": 121.4737},
            "destination": {"label": "公司", "latitude": 31.22, "longitude": 121.45},
        },
        headers=auth(h.token_a) | {"If-Match": f'"{plan["revision"]}"'},
    )
    assert updated.status_code == 200, updated.text
    new_revision = updated.json()["revision"]
    assert new_revision == plan["revision"] + 1

    weather = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=auth(h.token_a)
    )
    assert weather.status_code == 200
    assert weather.json()["matches_current_plan"] is False
    assert weather.json()["current_plan_revision"] == new_revision

    advisory = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=auth(h.token_a),
    )
    assert advisory.status_code == 200
    body = advisory.json()
    assert body["plan_revision"] == plan["revision"]
    assert body["matches_current_plan"] is False
    assert body["current_plan_revision"] == new_revision
    # Latest keeps its old meaning: the record is still returned, just labelled.
    assert body["is_current_service_date"] is True

    detail = await h.client.get(
        f"/api/v1/advisories/{body['id']}", headers=auth(h.token_a)
    )
    assert detail.status_code == 200
    assert detail.json()["matches_current_plan"] is False


@pytest.mark.asyncio
async def test_weather_and_advisory_expire_by_clock(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    await full_check(h, plan["id"], "weather-expiry-0001")
    await weather_only_check(h, plan["id"], "weather-expiry-0002")

    h.clock.advance(hours=2)

    weather = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/weather", headers=auth(h.token_a)
    )
    assert weather.status_code == 200
    assert weather.json()["is_stale"] is True

    advisory = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=auth(h.token_a),
    )
    assert advisory.status_code == 200
    assert advisory.json()["is_expired"] is True
