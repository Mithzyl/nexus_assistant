"""iOS snapshot, pending actions, estimate state, conflict ETA and revert."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from assistant_api.models import ContentSource, Reminder, ScheduleEvent, User
from assistant_api.services.matter_conflicts import evaluate_conflict
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


async def source_id(harness, *, assistant_enabled: bool = True, source_type: str = "calendar") -> str:
    response = await harness.client.post(
        "/api/v1/data-sources",
        json={
            "source_type": source_type,
            "device_id": f"device-{source_type}",
            "display_name": f"测试 {source_type}",
            "upload_fields": ["title", "start", "end", "location", "status"],
            "sync_enabled": True,
            "assistant_enabled": assistant_enabled,
        },
        headers=auth(harness.token_a),
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def snapshot(source: str, batch: str, records: list[dict], removed: list[str] | None = None) -> dict:
    return {
        "source_id": source,
        "batch_id": batch,
        "records": records,
        "removed_external_ids": removed or [],
    }


@pytest.mark.asyncio
async def test_ios_snapshot_is_idempotent_and_version_guarded(harness):
    h = harness
    source = await source_id(h)
    day = datetime.now(ZONE).date() + timedelta(days=1)
    newer = at(day, 9)
    older = newer - timedelta(hours=1)
    record = {
        "external_id": "evt-1",
        "last_modified_at": newer.isoformat(),
        "title": "周会",
        "starts_at": at(day, 10).isoformat(),
        "ends_at": at(day, 11).isoformat(),
        "location": "会议室",
    }
    first = await h.client.post(
        "/api/v1/sync/ios/snapshot", json=snapshot(source, "ios-batch-0001", [record]), headers=auth(h.token_a)
    )
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 1
    assert first.json()["source"] == "ios_eventkit"

    replay = await h.client.post(
        "/api/v1/sync/ios/snapshot", json=snapshot(source, "ios-batch-0001", [record]), headers=auth(h.token_a)
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["created"] == 1

    stale = {**record, "last_modified_at": older.isoformat(), "title": "旧标题"}
    stale_result = await h.client.post(
        "/api/v1/sync/ios/snapshot", json=snapshot(source, "ios-batch-0002", [stale]), headers=auth(h.token_a)
    )
    assert stale_result.json()["unchanged"] == 1

    updated = {**record, "title": "周会（改）", "last_modified_at": (newer + timedelta(hours=1)).isoformat()}
    update_result = await h.client.post(
        "/api/v1/sync/ios/snapshot", json=snapshot(source, "ios-batch-0003", [updated]), headers=auth(h.token_a)
    )
    assert update_result.json()["updated"] == 1

    # Absent from a batch is not a deletion.
    absent = await h.client.post(
        "/api/v1/sync/ios/snapshot", json=snapshot(source, "ios-batch-0004", []), headers=auth(h.token_a)
    )
    assert absent.status_code == 200
    async with h.app.state.session_factory() as db:
        rows = (await db.scalars(select(ScheduleEvent).where(ScheduleEvent.source_external_id == "evt-1"))).all()
        assert len(rows) == 1 and rows[0].deleted_at is None
        assert rows[0].title == "周会（改）"
        assert rows[0].sync_status == "ios_synced"
        assert rows[0].source_updated_at is not None

    removed = await h.client.post(
        "/api/v1/sync/ios/snapshot",
        json=snapshot(source, "ios-batch-0005", [], ["evt-1"]),
        headers=auth(h.token_a),
    )
    assert removed.json()["removed"] == 1
    today = await h.client.get(f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a))
    assert today.status_code == 200
    assert all(item["schedule_id"] != str(rows[0].schedule_id) for item in today.json()["schedules"])


@pytest.mark.asyncio
async def test_snapshot_rejects_malformed_and_unauthorized_sources(harness):
    h = harness
    source = await source_id(h)
    bad = await h.client.post(
        "/api/v1/sync/ios/snapshot",
        json=snapshot(source, "ios-batch-bad-01", [{"external_id": "x", "last_modified_at": at(datetime.now(ZONE).date(), 9).isoformat()}]),
        headers=auth(h.token_a),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "snapshot_record_invalid"
    assert (
        await h.client.post(
            "/api/v1/sync/ios/snapshot",
            json=snapshot(source, "ios-batch-bad-02", []),
            headers=auth(h.token_b),
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_conflict_eta_and_source_authorization(harness):
    h = harness
    user = None
    async with h.app.state.session_factory() as db:
        user = await db.get(User, h.user_a)
        assert user is not None

    day = datetime.now(ZONE).date() + timedelta(days=2)
    source = await source_id(h, assistant_enabled=False)
    async with h.app.state.session_factory() as db:
        db.add(
            ScheduleEvent(
                user_id=h.user_a,
                title="上一场（带坐标）",
                starts_at=at(day, 13),
                ends_at=at(day, 13, 45),
                latitude=31.2,
                longitude=121.4,
                status="scheduled",
                source="web_native",
                sync_status="local_only",
            )
        )
        await db.commit()

    async with h.app.state.session_factory() as db:
        result = await evaluate_conflict(
            db,
            h.app.state.commute_runtime,
            user=user,
            starts_at=at(day, 14, 15),
            ends_at=at(day, 15),
            latitude=31.3,
            longitude=121.5,
            window_hours=3,
            min_gap_seconds=900,
        )
    # Previous ends 13:45, proposal 14:15: 30 min gap, ETA 30 min + 15 min slack.
    assert result["has_conflict"] is True
    assert result["conflicts"][0]["eta_status"] == "available"
    assert result["conflicts"][0]["eta_seconds"] == 1800
    assert len(result["options"]) == 3

    # Second day: the web-native neighbour is far away; only the imported Apple
    # event is close. It must be ignored while its source is unauthorized.
    day2 = day + timedelta(days=1)
    async with h.app.state.session_factory() as db:
        db.add(
            ScheduleEvent(
                user_id=h.user_a,
                title="上午安排",
                starts_at=at(day2, 11),
                ends_at=at(day2, 12),
                status="scheduled",
                source="web_native",
                sync_status="local_only",
            )
        )
        db.add(
            ScheduleEvent(
                user_id=h.user_a,
                title="苹果导入日程",
                starts_at=at(day2, 14, 30),
                ends_at=at(day2, 14, 45),
                data_source_id=source,
                status="scheduled",
                source="ios_eventkit",
                sync_status="ios_synced",
            )
        )
        await db.commit()

    async with h.app.state.session_factory() as db:
        unauthorized = await evaluate_conflict(
            db,
            h.app.state.commute_runtime,
            user=user,
            starts_at=at(day2, 15),
            ends_at=at(day2, 16),
            window_hours=3,
            min_gap_seconds=1200,
        )
    assert unauthorized["has_conflict"] is False
    assert "苹果导入日程" not in [item["title"] for item in unauthorized["conflicts"]]

    patched = await h.client.patch(
        f"/api/v1/data-sources/{source}", json={"assistant_enabled": True}, headers=auth(h.token_a)
    )
    assert patched.status_code == 200
    async with h.app.state.session_factory() as db:
        authorized = await evaluate_conflict(
            db,
            h.app.state.commute_runtime,
            user=user,
            starts_at=at(day2, 15),
            ends_at=at(day2, 16),
            window_hours=3,
            min_gap_seconds=1200,
        )
    assert authorized["has_conflict"] is True
    assert any("苹果导入日程" in item["title"] for item in authorized["conflicts"])


@pytest.mark.asyncio
async def test_estimate_reports_real_provider_state(harness):
    h = harness
    body = {
        "origin": {"latitude": 31.2, "longitude": 121.4},
        "destination": {"latitude": 31.3, "longitude": 121.5},
        "mode": "driving",
        "depart_at": datetime.now(UTC).isoformat(),
    }
    response = await h.client.post("/api/v1/commute/estimate", json=body, headers=auth(h.token_a))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "available"
    assert response.json()["duration_seconds"] == 1800
    assert response.json()["source"] == "server_provider"

    class Unavailable:
        available = False

    runtime = h.app.state.commute_runtime
    original = runtime.eta
    runtime.eta = Unavailable()
    try:
        missing = await h.client.post("/api/v1/commute/estimate", json=body, headers=auth(h.token_a))
    finally:
        runtime.eta = original
    assert missing.status_code == 200
    assert missing.json()["status"] == "unavailable"
    assert missing.json()["duration_seconds"] is None
    assert missing.json()["reason_code"] == "route_provider_unavailable"


@pytest.mark.asyncio
async def test_pending_actions_list_confirmed_ios_changes_only(harness):
    h = harness
    day = datetime.now(ZONE).date() + timedelta(days=3)
    extraction = {
        "kind": "create",
        "title": "苹果回写事项",
        "goal": "验证待写入队列",
        "schedule": {
            "title": "回写日程",
            "starts_at": at(day, 10).isoformat(),
            "ends_at": at(day, 11).isoformat(),
        },
        "todos": [{"title": "回写提醒", "reminder_at": at(day, 9).isoformat()}],
        "confidence": 0.9,
    }
    h.fake.final_text = json.dumps(extraction, ensure_ascii=False)
    draft = (
        await h.client.post(
            "/api/v1/matters/intake",
            json={"text": "把这个回写到苹果", "idempotency_key": "matter-intake-ios-01"},
            headers=auth(h.token_a),
        )
    ).json()["draft"]
    confirmed = await h.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
        json={"expected_version": draft["version"], "acknowledge_conflict": True, "sync_to_ios": True},
        headers={**auth(h.token_a), "Idempotency-Key": "confirm-key-ios-01"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["schedule"]["sync_status"] == "pending_ios"

    pending = await h.client.get("/api/v1/sync/ios/pending-actions", headers=auth(h.token_a))
    assert pending.status_code == 200
    body = pending.json()
    assert body["items"], body
    assert all(item["sync_status"] == "pending_ios" for item in body["items"])
    assert "不代表已写入" in body["note"]
    types = {item["record_type"] for item in body["items"]}
    assert {"schedule", "reminder"} <= types


@pytest.mark.asyncio
async def test_revert_restores_the_previous_values(harness):
    h = harness
    extraction = {"kind": "create", "title": "可撤回事项", "goal": "原目标", "confidence": 0.9}
    h.fake.final_text = json.dumps(extraction, ensure_ascii=False)
    draft = (
        await h.client.post(
            "/api/v1/matters/intake",
            json={"text": "新建", "idempotency_key": "matter-intake-revert-01"},
            headers=auth(h.token_a),
        )
    ).json()["draft"]
    confirmed = (
        await h.client.post(
            f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
            json={"expected_version": draft["version"], "acknowledge_conflict": True},
            headers={**auth(h.token_a), "Idempotency-Key": "confirm-key-revert-01"},
        )
    ).json()
    matter = confirmed["matter"]
    change_id = confirmed["change_id"]

    update = {
        "kind": "update",
        "matter_id": matter["matter_id"],
        "changes_note": "改目标",
        "confidence": 0.9,
    }
    h.fake.final_text = json.dumps(update, ensure_ascii=False)
    draft2 = (
        await h.client.post(
            f"/api/v1/matters/{matter['matter_id']}/intake",
            json={"text": "补充", "idempotency_key": "matter-intake-revert-02"},
            headers=auth(h.token_a),
        )
    ).json()["draft"]
    updated = (
        await h.client.post(
            f"/api/v1/matters/drafts/{draft2['draft_id']}/confirm",
            json={"expected_version": draft2["version"], "acknowledge_conflict": True},
            headers={**auth(h.token_a), "Idempotency-Key": "confirm-key-revert-02"},
        )
    ).json()
    assert updated["change_id"] != change_id

    reverted = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/changes/{updated['change_id']}/revert",
        headers=auth(h.token_a),
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["matter"]["version"] == 3
    again = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/changes/{updated['change_id']}/revert",
        headers=auth(h.token_a),
    )
    assert again.status_code == 409
