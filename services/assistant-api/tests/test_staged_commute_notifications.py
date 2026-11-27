"""Drive staged commute reminders through SQLite, the scheduler and mocked Bark HTTP."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest
from sqlalchemy import select

from assistant_api.commute.services.notification_content import message, verified_boarding
from assistant_api.commute.scheduler import scheduler_tick
from assistant_api.commute.services.bark import BarkNotifier
from assistant_api.commute.services.push import claim_outbox
from assistant_api.commute.services.staged_notifications import last_accepted
from assistant_api.commute.worker import worker_tick
from assistant_api.commute.providers.base import ProviderUnavailable
from assistant_api.models import CommuteInstance, NotificationOutbox, PushAttempt
from conftest import auth
from tests.commute_flow import create_plan

ORIGIN = "https://example.invalid"
EVENING = datetime(2026, 10, 25, 14, tzinfo=UTC)
MORNING = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)


def assert_lock_screen_body(body):
    """Approximate four 16-character lock-screen rows without cutting the original text."""
    from unicodedata import east_asian_width

    lines = body.splitlines()
    assert len(lines) == 3 and all(lines) and len(body) <= 70
    assert sum((sum(2 if east_asian_width(c) in "FWA" else 1 for c in line) + 31) // 32
               for line in lines) <= 4
    assert all(phrase not in body for phrase in ("08:00", "倒推", "测算", "路线于", "…"))


async def setup(h, monkeypatch, responses=None):
    """Bind an isolated device and capture the real Bark POST at the transport boundary."""
    h.settings.bark_web_origin = ORIGIN
    h.settings.commute_auto_ai_enabled = False
    sent = []

    def receive(request):
        """Return deterministic delivery outcomes for the captured notification."""
        assert request.method == "POST" and request.url.path == "/push"
        sent.append(json.loads(request.content))
        code = (responses or [200])[min(len(sent) - 1, len(responses or [200]) - 1)]
        if code == "timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        return httpx.Response(code, json={"code": code})

    h.runtime.bark = BarkNotifier("test-staged-device", transport=httpx.MockTransport(receive))
    token = auth(h.token_a)
    bound = await h.client.post("/api/v1/commute/bark/binding", headers=token, json={"device_key": "test-staged-device"})
    assert bound.status_code == 201
    assert (await h.client.patch("/api/v1/commute/bark", headers=token,
                                json={"auto_reminders_enabled": True})).status_code == 200
    h.clock.set(EVENING)
    monkeypatch.setattr(h.eta, "available", True, raising=False)
    original = h.eta.fetch
    async def transit(**kwargs):
        """Supply fixed transfer and stop facts, without live times."""
        result = await original(**kwargs)
        return replace(result, transit_boarding={"line_id": "4", "line_name": "4路（往测试站）",
                                                "boarding_stop_id": "test-stop", "time_kind": "line_only"},
                       notification_details={"transit_lines": ["4路", "12路"], "boarding_stop_name": "市民广场",
                                             "alighting_stop_name": "图书馆", "walking_duration_seconds": 360})
    monkeypatch.setattr(h.eta, "fetch", transit)
    plan = await create_plan(h, h.token_a, weekdays=[1], arrival_local_time="08:00",
                             check_local_time="06:30", transport_mode="transit")
    return plan, sent


async def tick(h, at):
    """Drive the same durable scheduler and worker used by the unified service."""
    h.clock.set(at)
    async with h.app.state.session_factory() as db:
        queued = await scheduler_tick(db, h.settings, owner="test-stages", now=at,
                                     enqueue_enabled=False, staged_notifications=True)
    async with h.app.state.session_factory() as db:
        await worker_tick(db, h.runtime, worker_id="test-stage-worker", now=at)
    return queued


@pytest.mark.asyncio
async def test_evening_morning_recheck_are_independent(commute_harness, monkeypatch):
    """Send three independent phases across midnight and compare only accepted facts."""
    h = commute_harness
    plan, sent = await setup(h, monkeypatch)
    h.weather.probability = 0.6
    assert await tick(h, EVENING) == 1
    assert await tick(h, EVENING + timedelta(seconds=5)) == 0
    assert await tick(h, MORNING) == 1
    assert await tick(h, MORNING + timedelta(minutes=10)) == 1
    assert len(sent) == 2
    h.eta.duration_seconds += 12 * 60
    assert await tick(h, MORNING + timedelta(minutes=20)) == 1
    assert await tick(h, MORNING + timedelta(minutes=30)) == 0
    assert [item["level"] for item in sent] == ["passive", "active", "timeSensitive"]
    assert sent[2]["title"].endswith("（提前12分）")
    assert "原定" in sent[2]["body"].splitlines()[1] and "缓冲" in sent[2]["body"].splitlines()[1]
    for item in sent:
        assert item["url"] == f"{ORIGIN}/me/commute/{plan['id']}?service_date=2026-10-26"
        assert item["body"].splitlines()[0] == "乘4路转12路（市民广场→图书馆）"
        assert "带伞" in item["body"].splitlines()[2]
        assert "到站" not in item["body"] and "参考班次" not in item["body"]
        assert "subtitle" not in item and len(item["title"]) <= 15
        assert_lock_screen_body(item["body"])
        assert len(json.dumps(item, ensure_ascii=False).encode()) < 1024
    assert "步行约6分" in sent[0]["body"].splitlines()[1]
    async with h.app.state.session_factory() as db:
        rows = (await db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.reminder_sequence))).all()
        assert [row.notification_stage for row in rows] == ["evening", "morning", "recheck"]
        assert all(row.status == "accepted_by_bark" and row.attempt_count == 1 for row in rows)
        instance = await db.get(CommuteInstance, rows[-1].instance_id)
        assert await last_accepted(db, instance) == rows[-1].payload_json["notification_snapshot"]
    latest = await h.client.get(f"/api/v1/commute-plans/{plan['id']}/advisories/latest", headers=auth(h.token_a))
    assert latest.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [True, False])
async def test_morning_failure_and_first_valid_recovery(commute_harness, monkeypatch, preview):
    """Use a compatible preview or send a no-route message then the first real departure."""
    h = commute_harness
    _, sent = await setup(h, monkeypatch)
    if preview:
        await tick(h, EVENING)
    h.eta.fail = ProviderUnavailable("mock route down")
    await tick(h, MORNING)
    if preview:
        assert "今早未刷新" in sent[-1]["body"] and "昨晚方案" in sent[-1]["body"]
        assert "乘4路转12路" in sent[-1]["body"]
    else:
        assert "暂未算出" in sent[-1]["title"] and "08:00" not in sent[-1]["body"]
    h.eta.fail = None
    await tick(h, MORNING + timedelta(minutes=10))
    assert len(sent) == 2
    if not preview:
        assert sent[-1]["level"] == "timeSensitive"
        assert "07:20" in sent[-1]["title"]
        assert_lock_screen_body(sent[-1]["body"])


@pytest.mark.asyncio
async def test_manual_change_replaces_pending_and_skip_cancels(commute_harness, monkeypatch):
    """Save a manual change with the existing pending phase, then skip before network I/O."""
    from assistant_api.commute.services.checks import claim_check
    from assistant_api.commute.services.staged_notifications import execute_notification_check
    h = commute_harness
    plan, sent = await setup(h, monkeypatch)
    h.clock.set(MORNING)
    async with h.app.state.session_factory() as db:
        await scheduler_tick(db, h.settings, owner="test-stages", now=MORNING,
                             enqueue_enabled=False, staged_notifications=True)
        check = await claim_check(db, worker_id="prep-only", now=MORNING, lease_seconds=180)
        await execute_notification_check(db, h.runtime, check_id=check.check_id, worker_id="prep-only")
    path = f"/api/v1/commute-plans/{plan['id']}/days"
    payload = {"service_date": "2026-10-26"}
    day = (await h.client.post(path + "/query", headers=auth(h.token_a), json=payload)).json()
    changed = await h.client.patch(path, headers=auth(h.token_a), json={**payload,
        "arrival_local_time": "08:15", "idempotency_key": "manual-notify-day",
        "expected_plan_revision": day["revision"], "expected_matter_version": day["version"]})
    assert changed.status_code == 200 and changed.json()["applied"]
    async with h.app.state.session_factory() as db:
        row = await db.scalar(select(NotificationOutbox))
        assert row.status == "pending" and row.notification_stage == "morning"
        assert row.payload_json["notification_snapshot"]["target_arrival_at"] == "2026-10-26T00:15:00+00:00"
        assert "08:15" not in row.payload_json["aps"]["alert"]["body"]
        advisory_id = row.advisory_id
    skipped = await h.client.post(f"/api/v1/advisories/{advisory_id}/actions",
        headers={**auth(h.token_a), "Idempotency-Key": "skip-staged-notice"},
        json={"action": "skip_today", "installation_id": "isolated-test-client"})
    assert skipped.status_code == 200
    async with h.app.state.session_factory() as db:
        row = await db.scalar(select(NotificationOutbox))
        assert row.status == "cancelled" and row.last_error_code == "skipped_today"
        assert await claim_outbox(db, worker_id="skip-test", now=MORNING, lease_seconds=180) is None
    assert not sent


@pytest.mark.asyncio
async def test_bark_rejection_and_unknown_keep_logs_and_accepted_baseline(commute_harness, monkeypatch, capsys):
    """Retain rejection and timeout records without replay or a false accepted snapshot."""
    h = commute_harness
    _, sent = await setup(h, monkeypatch, [200, 400, "timeout"])
    await tick(h, EVENING)
    await tick(h, MORNING)
    h.eta.duration_seconds += 12 * 60
    await tick(h, MORNING + timedelta(minutes=10))
    await tick(h, MORNING + timedelta(minutes=20))
    assert len(sent) == 3
    async with h.app.state.session_factory() as db:
        rows = (await db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.reminder_sequence))).all()
        assert [row.status for row in rows] == ["accepted_by_bark", "failed", "unknown"]
        assert [row.attempt_count for row in rows] == [1, 1, 1]
        assert {attempt.result for attempt in (await db.scalars(select(PushAttempt))).all()} == {"accepted", "rejected", "unknown"}
        instance = await db.get(CommuteInstance, rows[-1].instance_id)
        assert await last_accepted(db, instance) == rows[0].payload_json["notification_snapshot"]
    log = capsys.readouterr().out
    assert "commute_notification_delivery" in log and "bark_network_unknown" in log
    assert "test-staged-device" not in log


@pytest.mark.parametrize(("kind", "expected", "omits"), [
    ("line_only", "乘4路", "07:24"),
    ("first_service", "4路首班06:00", "预计"),
    ("scheduled", "参考4路07:24班次", "预计"),
    ("live", "4路预计07:25到站", "参考"),
    ("expired_live", "参考4路07:24班次", "预计"),
])
def test_boarding_sources_and_lock_screen_lengths(kind, expected, omits):
    """Only a valid real-time observation can produce a real-time clock label."""
    from types import SimpleNamespace

    plan = SimpleNamespace(timezone="Asia/Shanghai")
    service_date = datetime(2026, 10, 26).date()
    stamp = "2026-10-26T"
    boarding = {"provider": "isolated", "direction": "north", "boarding_stop_id": "stop",
                "line_id": "a", "line_name": "4路", "time_kind": kind}
    if kind == "first_service":
        boarding["station_first_at"] = stamp + "06:00:00+08:00"
    if kind in {"line_only", "scheduled", "live", "expired_live"}:
        boarding["scheduled_boarding_at"] = stamp + "07:24:00+08:00"
    if kind in {"live", "expired_live"}:
        boarding.update(time_kind="live", predicted_boarding_at=stamp + "07:25:00+08:00",
                        trip_id="fixed-trip", observed_at=stamp + "06:28:00+08:00",
                        valid_until=stamp + ("06:33:00+08:00" if kind == "live" else "06:29:00+08:00"))
    value = verified_boarding(boarding, plan=plan, day=service_date, now=MORNING)
    title, body = message(plan, "morning", {
        "departure_at": stamp + "07:15:00+08:00", "target_arrival_at": stamp + "08:00:00+08:00",
        "duration_seconds": 2100, "buffer_seconds": 600, "boarding": value,
        "weather": {"hint": "12℃｜小雨，带伞"},
    }, None)
    assert expected in body.splitlines()[0] and omits not in body.splitlines()[0]
    assert len(title) <= 15 and "带伞" in body.splitlines()[2]
    assert_lock_screen_body(body)


@pytest.mark.parametrize(("details", "expected_route"), [
    ({"transit_lines": ["4路"], "boarding_stop_name": "白沙站", "alighting_stop_name": "图书馆"},
     "乘4路（白沙站→图书馆）"),
    ({"transit_lines": ["4路", "12路"], "boarding_stop_name": "很长的起点站" * 8,
      "alighting_stop_name": "很长的终点站" * 8}, "乘4路转12路"),
    ({"transit_lines": ["地铁环城超长线路" * 8, "12路"]}, "公交｜查看路线"),
    ({}, "乘4路"),
])
def test_transit_copy_keeps_real_names_or_omits_overlong_details(details, expected_route):
    """Use measured walking distance if time is missing and drop long names as whole facts."""
    from types import SimpleNamespace

    title, body = message(SimpleNamespace(timezone="Asia/Shanghai"), "evening", {
        "mode": "transit",
        "departure_at": "2026-10-26T07:15:00+08:00",
        "target_arrival_at": "2026-10-26T08:00:00+08:00",
        "saved_at": "2026-10-25T22:00:00+08:00",
        "duration_seconds": 1980, "buffer_seconds": 720,
        "walking_distance_meters": 600,
        "boarding": {"line_name": "4路（全线终点--非下车站）"},
        "route_details": details, "weather": {"hint": "11℃｜小雨，带伞"},
    }, None)
    assert title == "明早07:15出发"
    assert body.splitlines() == [expected_route, "含步行600米｜全程33分+缓冲12分", "11℃｜小雨，带伞"]
    assert_lock_screen_body(body)


@pytest.mark.parametrize(("mode", "distance", "road", "expected_route"), [
    ("driving", 8400, "沿江路", "驾车8.4公里｜经沿江路"),
    ("walking", 600, None, "步行600米"),
    ("bicycling", 1500, "滨江东路", "骑行1.5公里｜经滨江东路"),
    ("electrobike", None, None, "电动车"),
])
def test_non_transit_copy_shows_only_measured_distance_and_road(mode, distance, road, expected_route):
    """Show the selected mode and known route facts without repeating a walking-only distance."""
    from types import SimpleNamespace

    _, body = message(SimpleNamespace(timezone="Asia/Shanghai"), "morning", {
        "mode": mode,
        "departure_at": "2026-10-26T07:15:00+08:00",
        "duration_seconds": 1980, "buffer_seconds": 720,
        "distance_meters": distance, "walking_distance_meters": distance if mode == "walking" else None,
        "route_details": {"main_road": road}, "weather": {"hint": "11℃｜添衣"},
    }, None)
    assert body.splitlines() == [expected_route, "全程33分+缓冲12分", "11℃｜添衣"]
    assert_lock_screen_body(body)


@pytest.mark.asyncio
async def test_configurable_evening_stage_does_not_fetch_a_model(commute_harness, monkeypatch):
    """Honor the plan's configurable local time and quiet or normal Bark choice."""
    from assistant_api.models import CommutePlan
    h = commute_harness
    plan, sent = await setup(h, monkeypatch)
    async with h.app.state.session_factory() as db:
        saved = await db.get(CommutePlan, plan["id"])
        saved.evening_notice_local_time = "22:20"
        saved.evening_notice_level = "active"
        await db.commit()
    assert await tick(h, EVENING) == 0
    assert await tick(h, EVENING + timedelta(minutes=20)) == 1
    assert len(sent) == 1 and sent[0]["level"] == "active"
    assert not h.advice_text.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rain", "line"])
async def test_actionable_rain_or_line_change_without_time_shift(commute_harness, monkeypatch, change):
    """Alert for a new action when the recommended departure time has not moved."""
    from assistant_api.models import CommutePlan
    h = commute_harness
    plan, sent = await setup(h, monkeypatch)
    async with h.app.state.session_factory() as db:
        (await db.get(CommutePlan, plan["id"])).rain_extra_buffer_minutes = 0
        await db.commit()
    await tick(h, EVENING)
    await tick(h, MORNING)
    if change == "rain":
        h.weather.probability = 0.8
    else:
        first = h.eta.fetch
        async def changed_route(**kwargs):
            """Switch the primary line without changing the estimate."""
            current = await first(**kwargs)
            return replace(current, transit_boarding={**current.transit_boarding, "line_id": "12", "line_name": "12路"},
                           notification_details={"transit_lines": ["12路"]})
        monkeypatch.setattr(h.eta, "fetch", changed_route)
    await tick(h, MORNING + timedelta(minutes=10))
    assert len(sent) == 3 and sent[-1]["level"] == "timeSensitive"
    assert ("带伞" if change == "rain" else "改乘12路") in sent[-1]["body"]


@pytest.mark.asyncio
async def test_pending_confirmation_prevents_same_plan_recheck_duplicate(commute_harness, monkeypatch):
    """When the morning notice is pending, let it carry the latest facts before comparing changes."""
    from assistant_api.commute.services.checks import claim_check
    from assistant_api.commute.services.staged_notifications import execute_notification_check
    h = commute_harness
    _, sent = await setup(h, monkeypatch)
    h.clock.set(MORNING)
    async with h.app.state.session_factory() as db:
        await scheduler_tick(db, h.settings, owner="test-stages", now=MORNING,
                             enqueue_enabled=False, staged_notifications=True)
        check = await claim_check(db, worker_id="prep-only", now=MORNING, lease_seconds=180)
        await execute_notification_check(db, h.runtime, check_id=check.check_id, worker_id="prep-only")
    h.eta.duration_seconds += 12 * 60
    await tick(h, MORNING + timedelta(minutes=10))
    assert [item["level"] for item in sent] == ["active"]
    assert "07:08" in sent[0]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("disable", "error_code"), [(True, "bark_not_enabled"), (False, "notification_expired")])
async def test_worker_rechecks_binding_and_expiry(commute_harness, monkeypatch, disable, error_code):
    """Do not call Bark if the owner disables delivery or the action is already stale."""
    from assistant_api.commute.services.checks import claim_check
    from assistant_api.commute.services.staged_notifications import execute_notification_check
    h = commute_harness
    _, sent = await setup(h, monkeypatch)
    h.clock.set(MORNING)
    async with h.app.state.session_factory() as db:
        await scheduler_tick(db, h.settings, owner="test-stages", now=MORNING,
                             enqueue_enabled=False, staged_notifications=True)
        check = await claim_check(db, worker_id="prep-only", now=MORNING, lease_seconds=180)
        await execute_notification_check(db, h.runtime, check_id=check.check_id, worker_id="prep-only")
    if disable:
        response = await h.client.patch("/api/v1/commute/bark", headers=auth(h.token_a),
                                        json={"auto_reminders_enabled": False})
        assert response.status_code == 200
    else:
        h.clock.set(MORNING + timedelta(hours=2))
    async with h.app.state.session_factory() as db:
        await worker_tick(db, h.runtime, worker_id="final-delivery", now=h.clock())
        row = await db.scalar(select(NotificationOutbox))
        assert row.last_error_code == error_code
        assert row.status == ("cancelled" if disable else "expired")
    assert not sent
