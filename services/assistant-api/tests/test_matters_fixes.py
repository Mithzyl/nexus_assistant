"""Regression tests for the source-review findings."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from assistant_api.core.config import Settings
from assistant_api.models import (
    Matter,
    MatterDraft,
    Reminder,
    ScheduleEvent,
)
from assistant_api.services import tool_service
from assistant_api.services.tool_service import CalendarArguments
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


def day_after(days: int = 2):
    return datetime.now(ZONE).date() + timedelta(days=days)


async def make_source(harness, *, fields: list[str] | None = None, scopes: list[str] | None = None, assistant: bool = True, source_type: str = "calendar") -> str:
    response = await harness.client.post(
        "/api/v1/data-sources",
        json={
            "source_type": source_type,
            "device_id": f"dev-{datetime.now(UTC).timestamp()}",
            "display_name": "回归来源",
            "scope_keys": scopes or [],
            "upload_fields": fields or ["title", "start", "end", "location", "status"],
            "sync_enabled": True,
            "assistant_enabled": assistant,
        },
        headers=auth(harness.token_a),
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def snap(source: str, batch: str, records: list[dict], removed: list[str] | None = None) -> dict:
    return {"source_id": source, "batch_id": batch, "records": records, "removed_external_ids": removed or []}


async def intake(harness, key: str, text: str, body: dict, matter_id: str | None = None):
    harness.fake.final_text = json.dumps(body, ensure_ascii=False)
    url = f"/api/v1/matters/{matter_id}/intake" if matter_id else "/api/v1/matters/intake"
    return await harness.client.post(
        url, json={"text": text, "idempotency_key": key}, headers=auth(harness.token_a)
    )


async def confirm(harness, draft: dict, key: str, **overrides):
    payload = {
        "expected_version": draft["version"],
        "acknowledge_conflict": True,
        "create_schedule": True,
    }
    payload.update(overrides)
    return await harness.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
        json=payload,
        headers={**auth(harness.token_a), "Idempotency-Key": key},
    )


async def setup_matter_with_previous(harness, day):
    """Matter whose meeting is 14:00; a previous appointment ends 13:55."""

    extraction = {
        "kind": "create",
        "title": "客户面谈",
        "goal": "谈合作",
        "schedule": {
            "title": "客户面谈",
            "starts_at": at(day, 15).isoformat(),
            "ends_at": at(day, 16).isoformat(),
        },
        "confidence": 0.9,
    }
    draft = (await intake(harness, f"key-create-{day}", "创建", extraction)).json()["draft"]
    matter = (await confirm(harness, draft, f"confirm-create-{day}")).json()["matter"]
    async with harness.app.state.session_factory() as db:
        db.add(
            ScheduleEvent(
                user_id=harness.user_a,
                title="牙医",
                starts_at=at(day, 13),
                ends_at=at(day, 13, 55),
                status="scheduled",
                source="web_native",
                sync_status="local_only",
            )
        )
        await db.commit()
    update = {
        "kind": "update",
        "matter_id": matter["matter_id"],
        "changes_note": "改到两点",
        "schedule": {
            "title": "客户面谈",
            "starts_at": at(day, 14).isoformat(),
            "ends_at": at(day, 15).isoformat(),
            "location": "陆家嘴",
        },
        "confidence": 0.9,
    }
    draft2 = (
        await intake(harness, f"key-update-{day}", "改到两点", update, matter_id=matter["matter_id"])
    ).json()["draft"]
    return matter, draft2


@pytest.mark.asyncio
async def test_patch_draft_handles_non_empty_schedule_and_todos(harness):
    h = harness
    day = day_after()
    extraction = {
        "kind": "create",
        "title": "草稿编辑",
        "goal": "原目标",
        "schedule": {"title": "面谈", "starts_at": at(day, 10).isoformat(), "ends_at": at(day, 11).isoformat()},
        "todos": [{"title": "原待办", "due_at": at(day, 9).isoformat()}],
        "confidence": 0.9,
    }
    draft = (await intake(h, "key-patch-01", "创建", extraction)).json()["draft"]
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "title": "草稿编辑（改）",
            "schedule": {
                "title": "改后面谈",
                "starts_at": at(day, 12).isoformat(),
                "ends_at": at(day, 13).isoformat(),
                "location": "新地点",
            },
            "todos": [{"title": "新待办", "reminder_at": at(day, 8).isoformat()}],
            "goal": None,
            "next_step": None,
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["version"] == draft["version"] + 1
    assert body["payload"]["title"] == "草稿编辑（改）"
    assert body["payload"]["schedule"]["title"] == "改后面谈"
    assert body["payload"]["schedule"]["location"] == "新地点"
    assert body["payload"]["schedule"]["latitude"] is None
    assert body["payload"]["todos"][0]["title"] == "新待办"
    assert body["payload"]["goal"] is None

    bad = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": body["version"],
            "schedule": {
                "title": "x",
                "starts_at": at(day, 15).isoformat(),
                "ends_at": at(day, 14).isoformat(),
            },
        },
        headers=auth(h.token_a),
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_patch_naive_time_is_anchored_in_user_timezone(harness):
    h = harness
    day = day_after()
    draft = (
        await intake(h, "key-naive-01", "创建", {"kind": "create", "title": "时区", "confidence": 0.9})
    ).json()["draft"]
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "schedule": {"title": "会议", "starts_at": f"{day.isoformat()}T15:00:00", "ends_at": f"{day.isoformat()}T16:00:00"},
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    stored = patched.json()["payload"]["schedule"]["starts_at"]
    assert stored.startswith(datetime.combine(day, time(15), tzinfo=ZONE).astimezone(UTC).strftime("%Y-%m-%dT%H"))


@pytest.mark.asyncio
async def test_intake_retry_reuses_first_interpretation(harness):
    h = harness
    body = {"kind": "create", "title": "重试事项", "goal": "同一次操作", "confidence": 0.9}
    first = await intake(h, "key-retry-01", "同一句话", body)
    second = await intake(h, "key-retry-01", "同一句话", body)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["draft"]["draft_id"] == second.json()["draft"]["draft_id"]
    assert second.json()["replayed"] is True
    async with h.app.state.session_factory() as db:
        runs = (await db.scalars(select(__import__("assistant_api.models", fromlist=["NexusRun"]).NexusRun))).all()
        assert len([r for r in runs if r.purpose == "matter_plan"]) == 1

    different = await intake(h, "key-retry-01", "另一句话", body)
    assert different.status_code == 409
    assert different.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_overlap_and_containment_are_detected(harness):
    from assistant_api.models import User
    from assistant_api.services.matter_conflicts import evaluate_conflict

    h = harness
    day = day_after()
    async with h.app.state.session_factory() as db:
        user = await db.get(User, h.user_a)
        db.add(
            ScheduleEvent(
                user_id=h.user_a,
                title="全天工作坊",
                starts_at=at(day, 9),
                ends_at=at(day, 18),
                status="scheduled",
                source="web_native",
                sync_status="local_only",
            )
        )
        await db.commit()
        result = await evaluate_conflict(
            db,
            h.app.state.commute_runtime,
            user=user,
            starts_at=at(day, 14),
            ends_at=at(day, 15),
            window_hours=3,
            min_gap_seconds=900,
        )
    assert result["has_conflict"] is True
    assert result["severity"] == "overlap"
    assert result["conflicts"][0]["reason"] == "overlap"


@pytest.mark.asyncio
async def test_each_conflict_option_is_actually_applied(harness):
    h = harness
    for option in ("A", "B", "C"):
        day = day_after(3 + ord(option))
        matter, draft = await setup_matter_with_previous(h, day)
        assert draft["conflicts"]["has_conflict"] is True, draft["conflicts"]
        applied = await h.client.post(
            f"/api/v1/matters/drafts/{draft['draft_id']}/apply-option",
            json={"expected_version": draft["version"], "option_id": option},
            headers=auth(h.token_a),
        )
        assert applied.status_code == 200, applied.text
        selected = applied.json()
        assert selected["payload"]["selected_option"] == option
        ack = option != "C"
        done = await confirm(h, selected, f"confirm-option-{option}-{day}", acknowledge_conflict=ack)
        if option == "C":
            assert done.status_code == 409
            done = await confirm(h, selected, f"confirm-option-{option}-{day}", acknowledge_conflict=True)
        assert done.status_code == 200, done.text
        result = done.json()
        detail = (
            await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
        ).json()
        schedule = detail["schedules"][0]
        if option == "A":
            assert schedule["starts_at"] != at(day, 14).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if option == "B":
            previous = [
                item
                for item in (
                    await h.client.get(f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a))
                ).json()["schedules"]
                if item["title"] == "牙医"
            ][0]
            assert previous["ends_at"] != at(day, 13, 55).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            assert any("联系" in todo["title"] for todo in detail["todos"]), detail["todos"]


@pytest.mark.asyncio
async def test_same_external_id_on_two_sources_does_not_collide(harness):
    h = harness
    day = day_after()
    source_a = await make_source(h)
    source_b = await make_source(h)
    record = {
        "external_id": "same-ext",
        "last_modified_at": at(day, 9).isoformat(),
        "title": "设备A",
        "starts_at": at(day, 10).isoformat(),
        "ends_at": at(day, 11).isoformat(),
    }
    assert (await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source_a, "batch-a-0001", [record]), headers=auth(h.token_a))).status_code == 200
    record_b = {**record, "title": "设备B"}
    assert (await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source_b, "batch-b-0001", [record_b]), headers=auth(h.token_a))).status_code == 200
    async with h.app.state.session_factory() as db:
        rows = (await db.scalars(select(ScheduleEvent).where(ScheduleEvent.source_external_id == "same-ext"))).all()
        assert {row.title for row in rows} == {"设备A", "设备B"}
    newer = {**record, "title": "设备A改", "last_modified_at": at(day, 12).isoformat()}
    await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source_a, "batch-a-0002", [newer]), headers=auth(h.token_a))
    async with h.app.state.session_factory() as db:
        rows = (await db.scalars(select(ScheduleEvent).where(ScheduleEvent.source_external_id == "same-ext"))).all()
        assert {row.title for row in rows} == {"设备A改", "设备B"}


@pytest.mark.asyncio
async def test_old_delete_does_not_override_newer_copy(harness):
    h = harness
    day = day_after()
    source = await make_source(h)
    newer = at(day, 12)
    record = {
        "external_id": "del-1",
        "last_modified_at": newer.isoformat(),
        "title": "新副本",
        "starts_at": at(day, 13).isoformat(),
        "ends_at": at(day, 14).isoformat(),
    }
    await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source, "batch-del-0001", [record]), headers=auth(h.token_a))
    old_delete = {
        "external_id": "del-1",
        "last_modified_at": (newer - timedelta(hours=1)).isoformat(),
        "deleted": True,
    }
    result = await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source, "batch-del-0002", [old_delete]), headers=auth(h.token_a))
    assert result.status_code == 200, result.text
    assert result.json()["unchanged"] == 1
    async with h.app.state.session_factory() as db:
        row = await db.scalar(select(ScheduleEvent).where(ScheduleEvent.source_external_id == "del-1"))
        assert row is not None and row.deleted_at is None


@pytest.mark.asyncio
async def test_field_and_scope_revocation_reach_new_copies_and_tools(harness):
    h = harness
    day = day_after()
    source = await make_source(h, scopes=["work"])
    record = {
        "external_id": "scope-1",
        "scope_key": "work",
        "last_modified_at": at(day, 9).isoformat(),
        "title": "工作日程",
        "starts_at": at(day, 10).isoformat(),
        "ends_at": at(day, 11).isoformat(),
        "location": "办公室",
    }
    assert (await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source, "scope-batch-01", [record]), headers=auth(h.token_a))).status_code == 200

    settings = Settings(environment="test", database_url="sqlite+aiosqlite:///:memory:")
    async with h.app.state.session_factory() as db:
        result = await tool_service.get_calendar_events(
            db, h.user_a, CalendarArguments(start_date=day, end_date=day), settings
        )
        assert [item["title"] for item in result["items"]] == ["工作日程"]

    await h.client.patch(
        f"/api/v1/data-sources/{source}",
        json={"upload_fields": ["title", "start"]},
        headers=auth(h.token_a),
    )
    async with h.app.state.session_factory() as db:
        result = await tool_service.get_calendar_events(
            db, h.user_id if hasattr(h, "user_id") else h.user_a,
            CalendarArguments(start_date=day, end_date=day), settings,
        )
        assert result["items"][0]["ends_at"] is None
        assert result["items"][0]["location"] is None

    await h.client.patch(
        f"/api/v1/data-sources/{source}",
        json={"scope_keys": []},
        headers=auth(h.token_a),
    )
    async with h.app.state.session_factory() as db:
        result = await tool_service.get_calendar_events(
            db, h.user_a, CalendarArguments(start_date=day, end_date=day), settings
        )
        assert result["items"] == []


@pytest.mark.asyncio
async def test_cached_draft_conflict_respects_later_revocation(harness):
    h = harness
    day = day_after()
    source = await make_source(h)
    record = {
        "external_id": "draft-1",
        "last_modified_at": at(day, 9).isoformat(),
        "title": "导入会议",
        "starts_at": at(day, 14).isoformat(),
        "ends_at": at(day, 15).isoformat(),
    }
    await h.client.post("/api/v1/sync/ios/snapshot", json=snap(source, "draft-batch-1", [record]), headers=auth(h.token_a))
    extraction = {
        "kind": "create",
        "title": "冲突事项",
        "goal": "验证撤权",
        "schedule": {
            "title": "我的会议",
            "starts_at": at(day, 14, 30).isoformat(),
            "ends_at": at(day, 15, 30).isoformat(),
        },
        "confidence": 0.9,
    }
    draft = (await intake(h, "key-cache-01", "创建", extraction)).json()["draft"]
    assert draft["conflicts"]["has_conflict"] is True
    await h.client.patch(
        f"/api/v1/data-sources/{source}", json={"assistant_enabled": False}, headers=auth(h.token_a)
    )
    fresh = (
        await h.client.get(f"/api/v1/matters/drafts/{draft['draft_id']}", headers=auth(h.token_a))
    ).json()
    assert fresh["conflicts"]["has_conflict"] is False


@pytest.mark.asyncio
async def test_repeated_confirmation_claims_the_draft_once(harness):
    h = harness
    draft = (
        await intake(h, "key-concurrent-01", "创建", {"kind": "create", "title": "重复确认", "confidence": 0.9})
    ).json()["draft"]
    # The conditional UPDATE on (status=preview, version) is what makes a real
    # concurrent pair safe; with the in-memory single connection we exercise the
    # same claim sequentially with two different keys.
    first = await confirm(h, draft, "confirm-concurrent-01")
    second = await confirm(h, draft, "confirm-concurrent-02")
    assert first.status_code == 200
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] in ("draft_already_confirmed", "preview_stale")
    async with h.app.state.session_factory() as db:
        matters = (await db.scalars(select(Matter).where(Matter.title == "重复确认"))).all()
        assert len(matters) == 1


@pytest.mark.asyncio
async def test_revert_refuses_to_override_later_changes(harness):
    h = harness
    draft = (
        await intake(h, "key-revert-late-01", "创建", {"kind": "create", "title": "撤回保护", "goal": "a", "confidence": 0.9})
    ).json()["draft"]
    confirmed = (await confirm(h, draft, "confirm-revert-late-01")).json()
    matter = confirmed["matter"]
    first_change = confirmed["change_id"]
    for index in (2, 3):
        update = {"kind": "update", "matter_id": matter["matter_id"], "changes_note": f"更新{index}", "confidence": 0.9}
        draft2 = (
            await intake(h, f"key-revert-late-{index}", "更新", update, matter_id=matter["matter_id"])
        ).json()["draft"]
        updated = (await confirm(h, draft2, f"confirm-revert-late-{index}")).json()
        matter = updated["matter"]
    response = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/changes/{first_change}/revert",
        json={"expected_version": matter["version"]},
        headers=auth(h.token_a),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "later_changes"


@pytest.mark.asyncio
async def test_todo_completion_and_reminder_removal_stay_consistent(harness):
    h = harness
    day = day_after()
    extraction = {
        "kind": "create",
        "title": "提醒一致性",
        "goal": "x",
        "todos": [{"title": "催合同", "reminder_at": at(day, 8).isoformat()}],
        "confidence": 0.9,
    }
    draft = (await intake(h, "key-reminder-01", "创建", extraction)).json()["draft"]
    confirmed = (await confirm(h, draft, "confirm-reminder-01")).json()
    matter = confirmed["matter"]
    todo = (
        await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    ).json()["todos"][0]
    async with h.app.state.session_factory() as db:
        reminders = (await db.scalars(select(Reminder).where(Reminder.todo_id == todo["todo_id"]))).all()
        assert reminders and reminders[0].status == "pending"

    done = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/todos/{todo['todo_id']}/complete",
        headers=auth(h.token_a),
    )
    assert done.status_code == 200
    async with h.app.state.session_factory() as db:
        reminder = await db.scalar(select(Reminder).where(Reminder.todo_id == todo["todo_id"]))
        assert reminder.status == "pending"
    await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/todos/{todo['todo_id']}/reopen",
        headers=auth(h.token_a),
    )
    await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/todos/{todo['todo_id']}/complete?cancel_reminders=true",
        headers=auth(h.token_a),
    )
    async with h.app.state.session_factory() as db:
        reminder = await db.scalar(select(Reminder).where(Reminder.todo_id == todo["todo_id"]))
        assert reminder.status == "cancelled"


@pytest.mark.asyncio
async def test_estimate_requires_login(harness):
    response = await harness.client.post(
        "/api/v1/commute/estimate",
        json={
            "origin": {"latitude": 31.2, "longitude": 121.4},
            "destination": {"latitude": 31.3, "longitude": 121.5},
            "depart_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 401
