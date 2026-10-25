"""Commute plan CRUD, revision control, privacy and manual refresh API behavior."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from assistant_api.models import (
    AdvisoryAction,
    CommuteAdvisory,
    CommuteCheck,
    CommuteEtaSnapshot,
    CommuteInstance,
    CommuteWeatherSnapshot,
    NotificationOutbox,
    PushAttempt,
)
from tests.commute_flow import create_plan, execute_one_check
from tests.commute_support import commute_plan_body


def headers(token: str, extra: dict | None = None) -> dict:
    result = {"Authorization": f"Bearer {token}"}
    result.update(extra or {})
    return result


@pytest.mark.asyncio
async def test_plan_crud_and_revision_conflict(commute_harness):
    h = commute_harness
    created = await create_plan(h, h.token_a)
    assert created["revision"] == 1
    assert created["origin"]["latitude"] == 31.2304
    plan_id = created["id"]

    listed = await h.client.get("/api/v1/commute-plans", headers=headers(h.token_a))
    assert listed.status_code == 200 and [p["id"] for p in listed.json()] == [plan_id]

    detail = await h.client.get(
        f"/api/v1/commute-plans/{plan_id}", headers=headers(h.token_a)
    )
    assert detail.status_code == 200
    assert detail.headers["etag"] == '"1"'
    assert detail.json()["created_at"].endswith("Z")

    body = commute_plan_body(name="新名字")
    missing = await h.client.put(
        f"/api/v1/commute-plans/{plan_id}", json=body, headers=headers(h.token_a)
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    conflict = await h.client.put(
        f"/api/v1/commute-plans/{plan_id}",
        json=body,
        headers=headers(h.token_a, {"If-Match": '"9"'}),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "revision_conflict"

    updated = await h.client.put(
        f"/api/v1/commute-plans/{plan_id}",
        json=body,
        headers=headers(h.token_a, {"If-Match": 'W/"1"'}),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert updated.json()["name"] == "新名字"
    assert updated.headers["etag"] == '"2"'


@pytest.mark.asyncio
async def test_invalid_plan_is_rejected_with_error_envelope(commute_harness):
    h = commute_harness
    body = commute_plan_body(check_local_time="10:00", arrival_local_time="09:00")
    response = await h.client.post(
        "/api/v1/commute-plans", json=body, headers=headers(h.token_a)
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["request_id"] == response.headers["x-request-id"]

    body = commute_plan_body()
    body["origin"]["latitude"] = 91
    bad_lat = await h.client.post(
        "/api/v1/commute-plans", json=body, headers=headers(h.token_a)
    )
    assert bad_lat.status_code == 422

    body = commute_plan_body()
    body["user_id"] = h.user_b
    spoof = await h.client.post(
        "/api/v1/commute-plans", json=body, headers=headers(h.token_a)
    )
    assert spoof.status_code == 422


@pytest.mark.asyncio
async def test_cross_user_plan_and_check_access_is_hidden(commute_harness):
    h = commute_harness
    created = await create_plan(h, h.token_a)
    plan_id = created["id"]

    for response in (
        await h.client.get(f"/api/v1/commute-plans/{plan_id}", headers=headers(h.token_b)),
        await h.client.get(
            f"/api/v1/commute-plans/{plan_id}/advisories/latest",
            headers=headers(h.token_b),
        ),
        await h.client.post(
            f"/api/v1/commute-plans/{plan_id}/checks",
            headers=headers(h.token_b, {"Idempotency-Key": "manual-key-1"}),
        ),
    ):
        assert response.status_code == 404

    manual = await h.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "manual-key-1"}),
    )
    assert manual.status_code == 202, manual.text
    check_id = manual.json()["check_id"]

    hidden = await h.client.get(
        f"/api/v1/checks/{check_id}", headers=headers(h.token_b)
    )
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_manual_check_idempotency_and_execution(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    plan_id = plan["id"]
    key = "manual-refresh-key-1"
    first = await h.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": key}),
    )
    assert first.status_code == 202
    assert first.headers["x-idempotent-replay"] == "false"
    assert first.json()["status"] == "queued"
    check_id = first.json()["check_id"]

    replay = await h.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": key}),
    )
    assert replay.status_code == 202
    assert replay.headers["x-idempotent-replay"] == "true"
    assert replay.json()["check_id"] == check_id

    missing_key = await h.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks", headers=headers(h.token_a)
    )
    assert missing_key.status_code == 422

    # The same idempotency key on another plan is a different scope.
    second_plan = await create_plan(h, h.token_a, name="第二通勤")
    scoped = await h.client.post(
        f"/api/v1/commute-plans/{second_plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": key}),
    )
    assert scoped.status_code == 202

    check = await execute_one_check(h)
    assert check is not None and check.status in {"succeeded", "degraded"}

    status = await h.client.get(
        f"/api/v1/checks/{check_id}", headers=headers(h.token_a)
    )
    assert status.status_code == 200
    assert status.json()["trigger"] == "manual"
    assert status.json()["advisory_id"] == check.advisory_id
    assert status.json()["advisory_created"] is True

    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan_id}/advisories/latest",
        headers=headers(h.token_a),
    )
    assert latest.status_code == 200, latest.text
    advisory = latest.json()
    assert advisory["eta_seconds"] == 1800
    assert advisory["recommended_departure_at"] is not None
    assert advisory["departure_status"] == "upcoming"
    assert advisory["weather"]["precipitation_probability_max"] == 0.2
    assert advisory["sources"][0]["attribution"]["url"].startswith("https://")
    assert advisory["explanation_source"] == "template"

    # Manual refresh never creates an outbox, even though the plan allows pushes.
    async with h.app.state.session_factory() as db:
        assert (await db.scalar(select(func.count()).select_from(NotificationOutbox))) == 0


@pytest.mark.asyncio
async def test_no_advisory_404_uses_contract_code(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    response = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=headers(h.token_a),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_ADVISORY"


@pytest.mark.asyncio
async def test_plan_delete_cascades_private_child_rows(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    plan_id = plan["id"]
    await h.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "delete-cleanup-key"}),
    )
    await execute_one_check(h)
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(CommuteAdvisory)) == 1
        assert await db.scalar(select(func.count()).select_from(CommuteCheck)) == 1
        assert await db.scalar(select(func.count()).select_from(CommuteInstance)) == 1
        assert await db.scalar(select(func.count()).select_from(CommuteWeatherSnapshot)) == 1

    deleted = await h.client.delete(
        f"/api/v1/commute-plans/{plan_id}", headers=headers(h.token_a)
    )
    assert deleted.status_code == 204
    async with h.app.state.session_factory() as db:
        for model in (
            CommuteAdvisory,
            CommuteCheck,
            CommuteInstance,
            CommuteWeatherSnapshot,
            CommuteEtaSnapshot,
            NotificationOutbox,
            PushAttempt,
            AdvisoryAction,
        ):
            assert await db.scalar(select(func.count()).select_from(model)) == 0, model
    gone = await h.client.get(
        f"/api/v1/commute-plans/{plan_id}", headers=headers(h.token_a)
    )
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_manual_check_accepts_and_persists_route_snapshot(commute_harness):
    h = commute_harness
    plan = (await h.client.post("/api/v1/commute-plans", json=commute_plan_body(), headers=headers(h.token_a))).json()
    snapshot = {
        "plan_revision": plan["revision"],
        "route_snapshot": {
            "provider": "stub",
            "origin": {"latitude": 31.2304, "longitude": 121.4737},
            "destination": {"latitude": 31.2200, "longitude": 121.4500},
            "transport_mode": "driving",
            "query_type": "departure_time",
            "requested_departure_at": "2026-10-23T00:15:00Z",
            "queried_at": "2026-10-23T00:00:00Z",
            "distance_meters": 12000,
            "duration_seconds": 1800,
            "duration_in_traffic_seconds": 2100,
            "eta_source": "calculate_eta",
            "eta_scope": "overall",
        },
    }
    response = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "route-snapshot-1"}),
        json=snapshot,
    )
    assert response.status_code == 202, response.text
    check_id = response.json()["check_id"]
    async with h.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        assert check.route_snapshot_json["duration_in_traffic_seconds"] == 2100
        assert check.route_snapshot_hash

    conflict = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "route-snapshot-1"}),
        json={**snapshot, "plan_revision": plan["revision"] + 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    # A different revision with a fresh key is rejected before a second check.
    stale = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "route-snapshot-2"}),
        json={**snapshot, "plan_revision": plan["revision"] + 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "plan_revision_conflict"


@pytest.mark.asyncio
async def test_manual_check_without_snapshot_uses_server_provider_snapshot(commute_harness):
    h = commute_harness
    plan = (await h.client.post("/api/v1/commute-plans", json=commute_plan_body(), headers=headers(h.token_a))).json()
    response = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "server-route-1"}),
    )
    assert response.status_code == 202, response.text
    check = await execute_one_check(h)
    assert check is not None
    assert check.route_snapshot_json is not None
    assert check.route_snapshot_json["source"] == "server_provider"
    assert check.route_snapshot_json["provider"] == h.runtime.eta.provider
    assert check.phase == "done"
    assert check.analysis_mode == "full"
    assert check.next_attempt_at is None
    async with h.app.state.session_factory() as db:
        advisory = await db.get(CommuteAdvisory, check.advisory_id)
        assert advisory is not None
        assert advisory.ai_analysis_json["status"] == "unavailable"
        assert advisory.ai_analysis_json["reason"] == "ai_nexus_not_configured"
        assert advisory.ai_analysis_json["route_source"] == "server_provider"


@pytest.mark.asyncio
async def test_manual_check_without_snapshot_and_no_provider_is_explicit(commute_harness):
    from assistant_api.commute.runtime import UnavailableEtaProvider

    h = commute_harness
    h.runtime.eta = UnavailableEtaProvider()
    plan = (await h.client.post("/api/v1/commute-plans", json=commute_plan_body(), headers=headers(h.token_a))).json()
    response = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "no-route-1"}),
    )
    assert response.status_code == 202, response.text
    check = await execute_one_check(h)
    assert check is not None
    assert check.status == "degraded"
    assert check.error_code is None
    assert check.ai_run_id is None
    async with h.app.state.session_factory() as db:
        advisory = await db.get(CommuteAdvisory, check.advisory_id)
        assert advisory.ai_analysis_json["status"] == "unavailable"
        assert advisory.ai_analysis_json["reason"] == "route_provider_unconfigured"


@pytest.mark.asyncio
async def test_scheduler_keeps_weather_checks_when_route_provider_is_absent(commute_harness):
    from assistant_api.commute.runtime import UnavailableEtaProvider
    from tests.commute_flow import run_full_auto_cycle

    h = commute_harness
    h.runtime.eta = UnavailableEtaProvider()
    await create_plan(h, h.token_a)
    check, _outbox = await run_full_auto_cycle(h)
    assert check is not None
    async with h.app.state.session_factory() as db:
        weather = await db.scalar(
            select(CommuteWeatherSnapshot).where(CommuteWeatherSnapshot.check_id == check.check_id)
        )
        assert weather is not None
        advisory = await db.get(CommuteAdvisory, check.advisory_id)
        assert advisory.ai_analysis_json["reason"] == "route_provider_unconfigured"
        assert check.ai_run_id is None


@pytest.mark.asyncio
async def test_runtime_status_is_authenticated_and_user_scoped(commute_harness):
    from assistant_api.commute.heartbeat import record_heartbeat

    h = commute_harness
    async with h.app.state.session_factory() as db:
        await record_heartbeat(db, component="scheduler", instance_id="s1", now=h.clock())
        await record_heartbeat(db, component="worker", instance_id="w1", now=h.clock())
    plan = await create_plan(h, h.token_a)
    check = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "runtime-check-1"}),
    )
    assert check.status_code == 202

    anonymous = await h.client.get("/api/v1/commute/runtime")
    assert anonymous.status_code == 401

    mine = await h.client.get("/api/v1/commute/runtime", headers=headers(h.token_a))
    assert mine.status_code == 200
    payload = mine.json()
    assert payload["server_time"].endswith("Z")
    assert payload["stale_after_seconds"] == h.settings.commute_heartbeat_stale_seconds
    assert payload["scheduler"]["state"] == "online"
    assert payload["worker"]["state"] == "online"
    assert payload["queue"]["queued"] == 1
    assert payload["latest_check"]["check_id"] == check.json()["check_id"]
    assert "instance_id" not in payload["latest_check"]

    other = await h.client.get("/api/v1/commute/runtime", headers=headers(h.token_b))
    assert other.status_code == 200
    assert other.json()["queue"]["queued"] == 0
    assert other.json()["latest_check"] is None


@pytest.mark.asyncio
async def test_weather_only_does_not_create_an_advisory_or_change_latest(commute_harness):
    h = commute_harness
    plan = await create_plan(h, h.token_a)
    response = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=headers(h.token_a, {"Idempotency-Key": "weather-only-1"}),
    )
    assert response.status_code == 202
    check_id = response.json()["check_id"]
    async with h.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        check.analysis_mode = "weather_only"
        await db.commit()
    result = await execute_one_check(h)
    assert result is not None
    assert result.status == "succeeded"
    assert result.advisory_id is None
    assert result.phase == "done"
    async with h.app.state.session_factory() as db:
        weather = await db.scalar(
            select(CommuteWeatherSnapshot).where(CommuteWeatherSnapshot.check_id == check_id)
        )
        assert weather is not None
    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=headers(h.token_a),
    )
    assert latest.status_code == 404
