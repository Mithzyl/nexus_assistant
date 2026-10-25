"""Scheduler/worker business flow with a fixed clock and controllable providers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommuteInstance,
    NotificationOutbox,
    PushDevice,
)
from tests.commute_flow import (
    create_plan,
    deliver_one_outbox,
    execute_one_check,
    run_full_auto_cycle,
    scheduler_run,
)


def plan_edit_body(plan: dict, **overrides) -> dict:
    body = {
        key: plan[key]
        for key in (
            "name",
            "enabled",
            "timezone",
            "weekdays",
            "arrival_local_time",
            "check_local_time",
            "transport_mode",
            "buffer_minutes",
            "rain_probability_threshold",
            "rain_extra_buffer_minutes",
            "notify_enabled",
            "origin",
            "destination",
        )
    }
    body.update(overrides)
    return body


def auth(token: str, extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(extra or {})
    return headers


async def register_device(h, token: str, installation_id="install-1", permission="authorized"):
    response = await h.client.put(
        f"/api/v1/push-devices/{installation_id}",
        json={"apns_token": "a" * 64, "notification_permission": permission},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def outbox_rows(h):
    async with h.app.state.session_factory() as db:
        return list((await db.scalars(select(NotificationOutbox))).all())


@pytest.mark.asyncio
async def test_first_auto_check_generates_advisory_and_one_apns_attempt(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    plan = await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)

    assert check is not None and check.status == "succeeded"
    assert check.trigger == "auto" and check.advisory_created is True
    assert outbox is not None and outbox.status == "accepted_by_bark"
    assert outbox.reminder_sequence == 1
    assert len(h.bark.sent) == 1
    assert h.push.messages == []

    latest = await h.client.get(
        f"/api/v1/commute-plans/{plan['id']}/advisories/latest",
        headers=auth(h.token_a),
    )
    assert latest.status_code == 200
    advisory = latest.json()
    assert advisory["recommended_departure_at"] == "2026-10-23T00:20:00Z"
    assert advisory["departure_status"] == "upcoming"
    assert advisory["weather"]["rain_risk"] is False
    assert advisory["quality"] == "fresh"
    assert "ROUTE_FRESH" in set(advisory["reason_codes"])

    # One logical reminder, not one per device.
    h.bark.sent.clear()
    await register_device(h, h.token_a, installation_id="install-2")
    second_check, second_outbox = await run_full_auto_cycle(
        h, now=h.clock() + timedelta(minutes=10)
    )
    assert second_check.advisory_created is False
    assert second_outbox is None
    assert len(await outbox_rows(h)) == 1


@pytest.mark.asyncio
async def test_duplicate_scheduler_and_workers_do_not_duplicate_checks(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    assert await scheduler_run(h) == 0
    async with h.app.state.session_factory() as db:
        checks = list((await db.scalars(select(CommuteCheck))).all())
    assert len(checks) == 1

    check = await execute_one_check(h)
    assert check is not None
    # A second worker claims nothing for the same check.
    assert await execute_one_check(h) is None


@pytest.mark.asyncio
async def test_restart_recovers_expired_worker_lease(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    async with h.app.state.session_factory() as db:
        check = await db.scalar(select(CommuteCheck))
        check.status = "running"
        check.lease_owner = "dead-worker"
        check.lease_expires_at = h.clock() - timedelta(seconds=1)
        await db.commit()
    recovered = await execute_one_check(h)
    assert recovered is not None and recovered.status in {"succeeded", "degraded"}


@pytest.mark.asyncio
async def test_twice_daily_cap_and_minimum_gap(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    # 07:00 local; target 09:00, check window 06:30.
    h.clock.set(datetime(2026, 10, 22, 23, 0, tzinfo=UTC))
    await create_plan(h, h.token_a, check_local_time="06:30")

    first, outbox1 = await run_full_auto_cycle(h)
    assert outbox1 is not None and outbox1.reminder_sequence == 1

    # Arrival ETA worsens from 30 to 45 minutes: departure moves 08:20 -> 08:05.
    h.eta.duration_seconds = 45 * 60
    h.clock.advance(minutes=10)
    second, outbox2 = await run_full_auto_cycle(h)
    assert second.advisory_created is True
    assert outbox2 is None  # only 10 minutes since the first logical reminder

    # Another 15 minutes; ETA worsens to 60 minutes: departure 07:50, still future.
    h.eta.duration_seconds = 60 * 60
    h.clock.advance(minutes=15)
    third, outbox3 = await run_full_auto_cycle(h)
    assert third.advisory_created is True
    assert outbox3 is not None and outbox3.reminder_sequence == 2

    # A fourth material change is allowed to update the advisory but never to
    # exceed the two-reminder daily cap.
    h.eta.duration_seconds = 75 * 60
    h.clock.advance(minutes=5)
    fourth, outbox4 = await run_full_auto_cycle(h)
    if fourth is not None:
        assert outbox4 is None
    assert len(await outbox_rows(h)) == 2


@pytest.mark.asyncio
async def test_rain_crossing_notifies_and_adds_extra_buffer(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    h.weather.probability = 0.2
    await create_plan(h, h.token_a)
    first, _ = await run_full_auto_cycle(h)
    assert first.status == "succeeded"

    h.weather.probability = 0.7
    h.clock.advance(minutes=16)
    check, outbox = await run_full_auto_cycle(h)
    assert check is not None and check.advisory_created is True
    assert outbox is not None and outbox.reminder_sequence == 2
    async with h.app.state.session_factory() as db:
        advisory = await db.scalar(
            select(CommuteAdvisory).order_by(CommuteAdvisory.generated_at.desc())
        )
    assert advisory.weather_extra_buffer_seconds == 600
    from assistant_api.commute.timeutil import ensure_aware

    assert ensure_aware(advisory.recommended_departure_at) == datetime(
        2026, 10, 23, 0, 10, tzinfo=UTC
    )
    assert "WEATHER_RAIN_THRESHOLD_MET" in [
        item["code"] for item in advisory.reason_codes_json
    ]


@pytest.mark.asyncio
async def test_weather_unavailable_but_eta_valid_yields_degraded_route_advice(commute_harness):
    h = commute_harness
    from assistant_api.commute.providers.base import ProviderUnavailable

    h.weather.fail = ProviderUnavailable(
        "weather down", code="weather_unavailable", retryable=False
    )
    await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    assert check is not None and check.status == "degraded"
    assert check.advisory_created is True
    assert outbox is not None  # first advisory still gets one attempt
    async with h.app.state.session_factory() as db:
        advisory = await db.scalar(select(CommuteAdvisory))
    assert advisory.quality == "degraded"
    assert advisory.eta_seconds == 1800
    assert advisory.recommended_departure_at is not None
    assert advisory.weather_json is None
    assert "天气暂不可用" in advisory.summary


@pytest.mark.asyncio
async def test_eta_unavailable_but_weather_valid_has_no_precise_departure(commute_harness):
    h = commute_harness
    from assistant_api.commute.providers.base import ProviderNoRoute

    h.eta.fail = ProviderNoRoute("no route", code="route_not_found")
    await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    assert check is not None and check.status == "degraded"
    async with h.app.state.session_factory() as db:
        advisory = await db.scalar(select(CommuteAdvisory))
    assert advisory.quality == "degraded"
    assert advisory.eta_seconds is None
    assert advisory.recommended_departure_at is None
    assert advisory.departure_status == "unknown"
    assert advisory.weather_json["precipitation_probability_max"] == 0.2
    assert outbox is not None


@pytest.mark.asyncio
async def test_both_providers_unavailable_fail_but_send_one_short_notice(commute_harness):
    h = commute_harness
    from assistant_api.commute.providers.base import ProviderAuthError

    h.weather.fail = ProviderAuthError("bad key", code="weather_auth_failed")
    h.eta.fail = ProviderAuthError("bad key", code="maps_auth_failed")
    await register_device(h, h.token_a)
    await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    assert check is not None and check.status == "failed"
    assert check.error_code == "providers_unavailable"
    assert check.advisory_id is not None
    async with h.app.state.session_factory() as db:
        advisory = await db.scalar(select(CommuteAdvisory))
    assert advisory.quality == "unavailable"
    assert advisory.recommended_departure_at is None
    assert advisory.eta_seconds is None
    assert outbox is not None and outbox.reminder_sequence == 1
    assert "不可用" in h.bark.sent[0][1]

    # No second unavailable notice on the next automatic check.
    h.clock.advance(minutes=10)
    check2, outbox2 = await run_full_auto_cycle(h)
    assert outbox2 is None
    assert len(await outbox_rows(h)) == 1


@pytest.mark.asyncio
async def test_provider_rate_limit_retries_then_succeeds(commute_harness):
    h = commute_harness
    from assistant_api.commute.providers.base import ProviderRateLimited

    h.eta.fail = ProviderRateLimited("slow down", code="maps_rate_limited", retry_after_seconds=0)
    await create_plan(h, h.token_a)
    assert await scheduler_run(h) == 1
    first = await execute_one_check(h)
    assert first is not None and first.status == "queued"
    assert first.error_code == "maps_rate_limited"
    async with h.app.state.session_factory() as db:
        assert (
            await db.scalar(select(CommuteAdvisory))
        ) is None

    h.eta.fail = None
    h.clock.advance(minutes=1)
    second = await execute_one_check(h)
    assert second is not None and second.status in {"succeeded", "degraded"}
    assert second.advisory_id is not None


@pytest.mark.asyncio
async def test_skip_today_and_plan_edit_cancel_work(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    plan = await create_plan(h, h.token_a)
    first, outbox = await run_full_auto_cycle(h)
    advisory_id = first.advisory_id
    assert advisory_id is not None

    skipped = await h.client.post(
        f"/api/v1/advisories/{advisory_id}/actions",
        json={"action": "skip_today", "installation_id": "install-1"},
        headers=auth(h.token_a, {"Idempotency-Key": "skip-key-1"}),
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["action"] == "skip_today"

    h.clock.advance(minutes=20)
    assert await scheduler_run(h) == 0
    async with h.app.state.session_factory() as db:
        instance = await db.scalar(select(CommuteInstance))
        assert instance.status == "skipped"
        pending = list(
            (
                await db.scalars(
                    select(NotificationOutbox).where(NotificationOutbox.status == "pending")
                )
            ).all()
        )
        assert pending == []

    # Editing the plan supersedes a queued check from the old revision.
    edited = await h.client.put(
        f"/api/v1/commute-plans/{plan['id']}",
        json=plan_edit_body(plan, name="改名"),
        headers=auth(h.token_a, {"If-Match": '"1"'}),
    )
    assert edited.status_code == 200, edited.text
    h.clock.set(datetime(2026, 10, 23, 0, 0, tzinfo=UTC))
    # New instance? same day is skipped; verify an old queued check is cancelled.
    async with h.app.state.session_factory() as db:
        check = CommuteCheck(
            user_id=h.user_a,
            plan_id=plan["id"],
            instance_id=instance.instance_id,
            plan_revision=1,
            trigger="auto",
            slot_index=999,
            status="queued",
        )
        db.add(check)
        await db.commit()
        check_id = check.check_id
    edited2 = await h.client.put(
        f"/api/v1/commute-plans/{plan['id']}",
        json=plan_edit_body(plan, name="再改"),
        headers=auth(h.token_a, {"If-Match": '"2"'}),
    )
    assert edited2.status_code == 200
    async with h.app.state.session_factory() as db:
        cancelled = await db.scalar(
            select(CommuteCheck).where(CommuteCheck.check_id == check_id)
        )
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_manual_refresh_does_not_suppress_first_auto_reminder(commute_harness):
    h = commute_harness
    await register_device(h, h.token_a)
    plan = await create_plan(h, h.token_a)
    manual = await h.client.post(
        f"/api/v1/commute-plans/{plan['id']}/checks",
        headers=auth(h.token_a, {"Idempotency-Key": "manual-before-auto"}),
    )
    assert manual.status_code == 202
    manual_check = await execute_one_check(h)
    assert manual_check is not None and manual_check.advisory_created is True
    assert await outbox_rows(h) == []

    scheduler_count = await scheduler_run(h)
    assert scheduler_count == 1
    auto_check, outbox = await run_full_auto_cycle(h)
    assert auto_check is not None and auto_check.trigger == "auto"
    assert auto_check.advisory_created is False
    assert outbox is not None and outbox.reminder_sequence == 1
    assert len(h.bark.sent) == 1
