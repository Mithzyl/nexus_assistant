"""Private calendar feeds, place candidates and linked route-result behavior."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar
from sqlalchemy import select

from assistant_api.models import Matter, MatterDraft, Reminder, ScheduleEvent
from assistant_api.main import _CalendarTokenLogFilter
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def test_calendar_feed_token_is_redacted_from_access_log_arguments():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1",
            "GET",
            "/api/v1/calendar/super-secret-token.ics",
            "1.1",
            200,
        ),
        exc_info=None,
    )
    _CalendarTokenLogFilter().filter(record)
    assert "super-secret-token" not in " ".join(str(item) for item in record.args)
    assert "<redacted>" in record.args[2]


async def create_matter(harness, title: str) -> dict:
    response = await harness.client.post(
        "/api/v1/matters",
        json={"title": title},
        headers=auth(harness.token_a),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_schedule(
    harness,
    *,
    matter_id: str | None,
    title: str,
    starts_at: datetime,
    source: str = "web_native",
    status: str = "scheduled",
) -> str:
    async with harness.app.state.session_factory() as db:
        row = ScheduleEvent(
            user_id=harness.user_a,
            matter_id=matter_id,
            title=title,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status=status,
            source=source,
            sync_status="local_only" if source == "web_native" else "ios_synced",
            latitude=31.2304,
            longitude=121.4737,
            coordinate_system="gcj02",
            location="上海",
            location_city="上海",
            location_source="amap_place",
            location_external_id="B000TEST",
        )
        db.add(row)
        await db.commit()
        return row.schedule_id


@pytest.mark.asyncio
async def test_place_candidates_and_linked_route_invalidate_after_schedule_edit(harness):
    h = harness
    places = await h.client.post(
        "/api/v1/commute/places/search",
        json={"query": "静安寺", "city": "上海", "search_type": "place"},
        headers=auth(h.token_a),
    )
    assert places.status_code == 200, places.text
    assert places.json()["ambiguous"] is True
    assert len(places.json()["candidates"]) == 2

    matter = await create_matter(h, "路线关联")
    starts_at = datetime.now(UTC) + timedelta(days=2)
    schedule_id = await add_schedule(
        h,
        matter_id=matter["matter_id"],
        title="客户面谈",
        starts_at=starts_at,
    )
    previous_id = await add_schedule(
        h,
        matter_id=matter["matter_id"],
        title="上一场会议",
        starts_at=starts_at - timedelta(hours=2),
    )
    measured = await h.client.post(
        "/api/v1/commute/estimate",
        json={
            "origin_schedule_id": previous_id,
            "destination": {
                "latitude": 31.2304,
                "longitude": 121.4737,
                "coordinate_system": "gcj02",
                "city": "上海",
            },
            "mode": "transit",
            "depart_at": starts_at.isoformat(),
            "schedule_id": schedule_id,
            "origin_basis": "user_confirmed",
        },
        headers=auth(h.token_a),
    )
    assert measured.status_code == 200, measured.text
    assert measured.json()["status"] == "available"
    assert measured.json()["origin_basis"] == "previous_schedule"

    day = starts_at.astimezone(ZONE).date().isoformat()
    today = await h.client.get(
        f"/api/v1/today?date={day}", headers=auth(h.token_a)
    )
    item = next(
        row for row in today.json()["schedules"] if row["schedule_id"] == schedule_id
    )
    assert item["route"]["status"] == "available"
    assert item["route"]["mode"] == "transit"

    direct = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/drafts",
        json={
            "idempotency_key": f"place-selection-{schedule_id}",
            "schedule_id": schedule_id,
        },
        headers=auth(h.token_a),
    )
    assert direct.status_code == 201, direct.text
    draft = direct.json()
    selected = places.json()["candidates"][1]
    foreign = await h.client.post(
        "/api/v1/commute/places/confirm",
        json={
            "draft_id": draft["draft_id"],
            "selection_token": selected["selection_token"],
        },
        headers=auth(h.token_b),
    )
    assert foreign.status_code == 404
    place_confirmed = await h.client.post(
        "/api/v1/commute/places/confirm",
        json={
            "draft_id": draft["draft_id"],
            "selection_token": selected["selection_token"],
        },
        headers=auth(h.token_a),
    )
    assert place_confirmed.status_code == 200, place_confirmed.text
    changed_schedule = place_confirmed.json()["payload"]["schedule"]
    assert changed_schedule["location"] == selected["name"]
    assert changed_schedule["location_external_id"] == selected["provider_place_id"]
    assert changed_schedule["coordinate_system"] == "gcj02"
    assert place_confirmed.json()["conflicts"] is not None

    still_formal = await h.client.get(
        f"/api/v1/today?date={day}", headers=auth(h.token_a)
    )
    before_confirm = next(
        row
        for row in still_formal.json()["schedules"]
        if row["schedule_id"] == schedule_id
    )
    assert before_confirm["route"]["status"] == "available"

    done = await h.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
        json={
            "expected_version": place_confirmed.json()["version"],
            "acknowledge_conflict": True,
            "create_schedule": True,
        },
        headers={
            **auth(h.token_a),
            "Idempotency-Key": f"place-confirm-{schedule_id}",
        },
    )
    assert done.status_code == 200, done.text

    today_after = await h.client.get(
        f"/api/v1/today?date={day}", headers=auth(h.token_a)
    )
    item_after = next(
        row
        for row in today_after.json()["schedules"]
        if row["schedule_id"] == schedule_id
    )
    assert item_after["route"]["status"] == "unverified"


@pytest.mark.asyncio
async def test_calendar_subscription_is_private_selected_and_revocable(harness):
    h = harness
    selected = await create_matter(h, "共享事项")
    other = await create_matter(h, "未选择事项")
    starts_at = datetime.now(UTC) + timedelta(days=3)
    shared_id = await add_schedule(
        h,
        matter_id=selected["matter_id"],
        title="共享日程",
        starts_at=starts_at,
    )
    await add_schedule(
        h,
        matter_id=selected["matter_id"],
        title="导入副本",
        starts_at=starts_at + timedelta(hours=2),
        source="ios_eventkit",
    )
    await add_schedule(
        h,
        matter_id=other["matter_id"],
        title="未选择日程",
        starts_at=starts_at + timedelta(hours=4),
    )
    await add_schedule(
        h,
        matter_id=selected["matter_id"],
        title="已取消日程",
        starts_at=starts_at + timedelta(hours=6),
        status="cancelled",
    )
    async with h.app.state.session_factory() as db:
        db.add(
            Reminder(
                user_id=h.user_a,
                matter_id=selected["matter_id"],
                title="独立提醒",
                trigger_at=starts_at - timedelta(minutes=15),
                status="pending",
                source="web_native",
                sync_status="local_only",
            )
        )
        db.add(
            MatterDraft(
                user_id=h.user_a,
                matter_id=selected["matter_id"],
                kind="update",
                status="preview",
                payload_json={"title": "草稿"},
            )
        )
        await db.commit()

    foreign = await h.client.post(
        "/api/v1/calendar-subscription",
        json={"matter_ids": [selected["matter_id"]]},
        headers=auth(h.token_b),
    )
    assert foreign.status_code == 404

    created = await h.client.post(
        "/api/v1/calendar-subscription",
        json={"matter_ids": [selected["matter_id"]]},
        headers=auth(h.token_a),
    )
    assert created.status_code == 201, created.text
    feed_url = created.json()["feed_url"]
    assert feed_url and "?" not in feed_url

    feed = await h.client.get(feed_url)
    assert feed.status_code == 200, feed.text
    assert feed.headers["content-type"].startswith("text/calendar")
    calendar = Calendar.from_ical(feed.content)
    events = [item for item in calendar.walk() if item.name == "VEVENT"]
    assert len(events) == 1
    assert str(events[0]["UID"]).startswith(f"pa-schedule-{shared_id}@")
    original_sequence = int(events[0]["SEQUENCE"])
    assert b"BEGIN:VALARM" not in feed.content
    assert b"BEGIN:VTODO" not in feed.content

    async with h.app.state.session_factory() as db:
        schedule = await db.get(ScheduleEvent, shared_id)
        schedule.title = "共享日程（更新）"
        await db.commit()
    updated_feed = await h.client.get(feed_url)
    updated_calendar = Calendar.from_ical(updated_feed.content)
    updated_event = [
        item for item in updated_calendar.walk() if item.name == "VEVENT"
    ][0]
    assert str(updated_event["UID"]).startswith(f"pa-schedule-{shared_id}@")
    assert str(updated_event["SUMMARY"]) == "共享日程（更新）"
    assert int(updated_event["SEQUENCE"]) >= original_sequence

    async with h.app.state.session_factory() as db:
        schedule = await db.get(ScheduleEvent, shared_id)
        schedule.status = "cancelled"
        schedule.deleted_at = datetime.now(UTC)
        await db.commit()
    cancelled_feed = await h.client.get(feed_url)
    cancelled_calendar = Calendar.from_ical(cancelled_feed.content)
    assert not [
        item for item in cancelled_calendar.walk() if item.name == "VEVENT"
    ]

    status = await h.client.get(
        "/api/v1/calendar-subscription", headers=auth(h.token_a)
    )
    assert status.status_code == 200
    assert status.json()["subscription"]["feed_url"] is None
    assert status.json()["subscription"]["last_read_at"] is not None

    rotated = await h.client.post(
        "/api/v1/calendar-subscription/rotate",
        json={"matter_ids": [selected["matter_id"]]},
        headers=auth(h.token_a),
    )
    assert rotated.status_code == 200, rotated.text
    assert (await h.client.get(feed_url)).status_code == 404
    new_url = rotated.json()["feed_url"]
    assert (await h.client.get(new_url)).status_code == 200

    revoked = await h.client.delete(
        "/api/v1/calendar-subscription", headers=auth(h.token_a)
    )
    assert revoked.status_code == 200, revoked.text
    assert "自行移除" in revoked.json()["note"]
    assert (await h.client.get(new_url)).status_code == 404

    async with h.app.state.session_factory() as db:
        schedules = (
            await db.scalars(
                select(ScheduleEvent).where(ScheduleEvent.matter_id == selected["matter_id"])
            )
        ).all()
        assert all(row.sync_status != "ios_synced" for row in schedules if row.source == "web_native")


@pytest.mark.asyncio
async def test_shortcut_upload_rejects_subscription_echo_and_question_outcome(harness):
    h = harness
    source = await h.client.post(
        "/api/v1/data-sources",
        json={
            "source_type": "calendar",
            "device_id": "shortcut-device",
            "display_name": "快捷指令日历",
            "upload_fields": ["title", "start", "end"],
        },
        headers=auth(h.token_a),
    )
    assert source.status_code == 201, source.text
    now = datetime.now(UTC)
    echo = await h.client.post(
        "/api/v1/sync/ios/snapshot",
        json={
            "source_id": source.json()["source_id"],
            "batch_id": "shortcut-echo-0001",
            "records": [
                {
                    "external_id": "pa-schedule-123@personal-assistant.local",
                    "last_modified_at": now.isoformat(),
                    "title": "订阅回传",
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(hours=1)).isoformat(),
                }
            ],
            "removed_external_ids": [],
        },
        headers=auth(h.token_a),
    )
    assert echo.status_code == 422
    assert echo.json()["error"]["code"] == "subscription_echo_not_allowed"

    credential = await h.client.post(
        "/api/v1/sync/ios/shortcut-tokens",
        json={
            "source_id": source.json()["source_id"],
            "display_name": "手动运行凭据",
        },
        headers=auth(h.token_a),
    )
    assert credential.status_code == 201, credential.text
    shortcut_token = credential.json()["credential"]
    accepted = await h.client.post(
        "/api/v1/sync/ios/snapshot",
        json={
            "source_id": source.json()["source_id"],
            "batch_id": "shortcut-upload-0002",
            "records": [
                {
                    "external_id": "event-kit-stable-id-1",
                    "last_modified_at": now.isoformat(),
                    "title": "手机日历",
                    "starts_at": (now + timedelta(days=1)).isoformat(),
                    "ends_at": (now + timedelta(days=1, hours=1)).isoformat(),
                }
            ],
            "removed_external_ids": [],
        },
        headers={"X-Shortcut-Token": shortcut_token},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["created"] == 1

    other_source = await h.client.post(
        "/api/v1/data-sources",
        json={
            "source_type": "calendar",
            "device_id": "another-shortcut-device",
            "display_name": "另一个日历",
            "upload_fields": ["title", "start", "end"],
        },
        headers=auth(h.token_a),
    )
    mismatch = await h.client.post(
        "/api/v1/sync/ios/snapshot",
        json={
            "source_id": other_source.json()["source_id"],
            "batch_id": "shortcut-mismatch-0001",
            "records": [],
            "removed_external_ids": [],
        },
        headers={"X-Shortcut-Token": shortcut_token},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "shortcut_token_source_mismatch"

    revoked = await h.client.delete(
        f"/api/v1/sync/ios/shortcut-tokens/{credential.json()['token_id']}",
        headers=auth(h.token_a),
    )
    assert revoked.status_code == 200, revoked.text
    rejected = await h.client.post(
        "/api/v1/sync/ios/snapshot",
        json={
            "source_id": source.json()["source_id"],
            "batch_id": "shortcut-upload-0003",
            "records": [],
            "removed_external_ids": [],
        },
        headers={"X-Shortcut-Token": shortcut_token},
    )
    assert rejected.status_code == 401

    h.fake.final_text = '{"intent":"question","answer":"可以直接回答。"}'
    question = await h.client.post(
        "/api/v1/matters/intake",
        json={"text": "这件事怎么办？", "idempotency_key": "question-outcome-01"},
        headers=auth(h.token_a),
    )
    assert question.status_code == 200, question.text
    assert question.json()["outcome"] == "answer"
    async with h.app.state.session_factory() as db:
        assert (
            await db.scalar(select(Matter).where(Matter.user_id == h.user_a))
        ) is None
