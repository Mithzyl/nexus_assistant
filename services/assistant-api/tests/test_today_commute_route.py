"""Exercise Today against routes written by the actual staged commute worker."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from assistant_api.commute.scheduler import scheduler_tick
from assistant_api.commute.services.bark import BarkNotifier
from assistant_api.commute.worker import worker_tick
from assistant_api.services import schedule_routes
from conftest import auth
from tests.commute_flow import create_plan


@pytest.mark.asyncio
async def test_today_commute_keeps_origin_and_historical_route_after_expiry(commute_harness, monkeypatch):
    """Keep the measured trip visible after 15 minutes without calling it current."""
    h = commute_harness
    morning = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    h.clock.set(morning)
    h.settings.bark_web_origin = "https://example.invalid"
    monkeypatch.setattr(h.eta, "available", True, raising=False)
    fetch = h.eta.fetch

    async def measured_transit(**request):
        """Provide a fixed map result with the same 15-minute basis as production."""
        return replace(await fetch(**request), timing_basis="provider_departure_time",
                       duration_seconds=1872, transfers_summary="4路 → 12路")

    def accept_bark(request):
        """Accept the isolated worker's notification without contacting the network."""
        assert request.url.path == "/push"
        return httpx.Response(200, json={"code": 200})

    monkeypatch.setattr(h.eta, "fetch", measured_transit)
    h.runtime.bark = BarkNotifier("isolated-test-device", transport=httpx.MockTransport(accept_bark))
    headers = auth(h.token_a)
    assert (await h.client.post("/api/v1/commute/bark/binding",
        headers=headers, json={"device_key": "isolated-test-device"})).status_code == 201
    assert (await h.client.patch("/api/v1/commute/bark", headers=headers,
        json={"auto_reminders_enabled": True})).status_code == 200
    plan = await create_plan(h, h.token_a, weekdays=[1], arrival_local_time="08:00",
                             check_local_time="06:30", transport_mode="transit")
    async with h.app.state.session_factory() as db:
        assert await scheduler_tick(db, h.settings, owner="today-test", now=morning,
                                    enqueue_enabled=False, staged_notifications=True) == 1
    async with h.app.state.session_factory() as db:
        await worker_tick(db, h.runtime, worker_id="today-test-worker", now=morning)

    ordinary = await h.client.post("/api/v1/schedules", headers=headers, json={
        "idempotency_key": "today-ordinary-trip", "title": "普通外出",
        "starts_at": "2026-10-26T10:00:00+08:00", "ends_at": "2026-10-26T10:30:00+08:00",
        "location": "图书馆",
    })
    assert ordinary.status_code == 201, ordinary.text

    route_clock = datetime.now(UTC)

    class RouteClock(datetime):
        """Advance the read clock after the real worker has saved the route."""

        @classmethod
        def now(cls, tz=None):
            """Return the test clock in the requested timezone."""
            return route_clock.astimezone(tz) if tz else route_clock.replace(tzinfo=None)

    monkeypatch.setattr(schedule_routes, "datetime", RouteClock)
    saved_now = route_clock
    for elapsed, expected in ((14, "available"), (16, "unverified")):
        route_clock = saved_now + timedelta(minutes=elapsed)
        response = await h.client.get("/api/v1/today?date=2026-10-26", headers=headers)
        assert response.status_code == 200, response.text
        schedules = {item["title"]: item for item in response.json()["schedules"]}
        route = schedules["上班通勤"]["route"]
        assert route["status"] == expected
        assert route["origin_known"] is True
        assert route["commute"]["plan_id"] == plan["id"]
        assert route["commute"]["service_date"] == "2026-10-26"
        assert route["commute"]["origin_label"] == "家"
        assert route["commute"]["destination_label"] == "公司"
        assert route["commute"]["saved_route"] == {
            "duration_seconds": 1872, "mode": "transit", "transfers_summary": "4路 → 12路",
        }
        if elapsed == 16:
            assert route["reason_code"] == "route_result_expired"
            assert route["duration_seconds"] is None and route["origin_basis"] is None
        assert schedules["普通外出"]["route"]["origin_known"] is False
        assert schedules["普通外出"]["route"]["commute"] is None
