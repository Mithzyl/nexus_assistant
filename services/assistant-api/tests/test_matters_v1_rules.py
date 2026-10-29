"""Targeted checks for the V1 lifecycle rules added from the completion report."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar
from sqlalchemy import select

from assistant_api.models import (
    Matter,
    MatterTodo,
    Reminder,
    ScheduleEvent,
)
from assistant_api.services import matter_plan
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def at(day, hour: int, minute: int = 0) -> datetime:
    """Build one Asia/Shanghai wall time and return it in UTC."""

    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


async def intake(harness, key: str, text: str, body: dict, matter_id: str | None = None):
    """Submit one fake-model matter result through the normal intake endpoint."""

    harness.fake.final_text = json.dumps(body, ensure_ascii=False)
    path = (
        f"/api/v1/matters/{matter_id}/intake"
        if matter_id
        else "/api/v1/matters/intake"
    )
    return await harness.client.post(
        path,
        json={"text": text, "idempotency_key": key},
        headers=auth(harness.token_a),
    )


async def confirm(harness, draft: dict, key: str, **overrides):
    """Confirm a preview with an explicit idempotency key."""

    body = {"expected_version": draft["version"], "acknowledge_conflict": True}
    body.update(overrides)
    return await harness.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
        json=body,
        headers={**auth(harness.token_a), "Idempotency-Key": key},
    )


async def create_simple_matter(harness, key: str, *, with_todo: bool = False):
    """Create one synthetic matter and return its intake and confirmation bodies."""

    future = datetime.now(UTC) + timedelta(days=3)
    extraction = {
        "kind": "create",
        "title": f"合成事项 {key}",
        "goal": "验证 V1 后端规则",
        "todos": (
            [
                {
                    "title": "合成待办",
                    "reminder_at": (future + timedelta(hours=1)).isoformat(),
                }
            ]
            if with_todo
            else []
        ),
        "confidence": 0.9,
    }
    intake_response = await intake(harness, key, "创建合成事项", extraction)
    assert intake_response.status_code == 201, intake_response.text
    intake_body = intake_response.json()
    confirmed = await confirm(harness, intake_body["draft"], f"confirm-{key}")
    assert confirmed.status_code == 200, confirmed.text
    return intake_body, confirmed.json()


@pytest.mark.asyncio
async def test_initial_delete_removes_only_untouched_created_records(harness):
    h = harness
    created_intake, created = await create_simple_matter(
        h, "initial-delete", with_todo=True
    )
    matter_id = created["matter"]["matter_id"]

    changes = await h.client.get(
        f"/api/v1/matters/{matter_id}/changes", headers=auth(h.token_a)
    )
    assert changes.status_code == 200, changes.text
    change = changes.json()[0]
    assert change["initial_delete_allowed"] is True
    assert change["revertable"] is False

    deleted = await h.client.delete(
        f"/api/v1/matters/{matter_id}/initial", headers=auth(h.token_a)
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["reminder_ids"]
    assert (
        await h.client.get(
            f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
        )
    ).status_code == 404
    assert (
        await h.client.get(
            f"/api/v1/matters/drafts/{created['draft']['draft_id']}",
            headers=auth(h.token_a),
        )
    ).status_code == 404
    draft_listing = await h.client.get(
        "/api/v1/matters/drafts", headers=auth(h.token_a)
    )
    assert all(
        item["draft_id"] != created["draft"]["draft_id"]
        for item in draft_listing.json()
    )
    hidden_history = await h.client.get(
        f"/api/v1/matters/intake/{created_intake['local_run_id']}",
        headers=auth(h.token_a),
    )
    assert hidden_history.status_code == 200
    assert hidden_history.json()["draft"] is None
    assert hidden_history.json()["outcome"] == "unknown"

    async with h.app.state.session_factory() as db:
        matter = await db.get(Matter, matter_id)
        todos = (
            await db.scalars(select(MatterTodo).where(MatterTodo.matter_id == matter_id))
        ).all()
        reminders = (
            await db.scalars(select(Reminder).where(Reminder.matter_id == matter_id))
        ).all()
        assert matter is not None and matter.deleted_at is not None
        assert all(row.deleted_at is not None for row in todos)
        assert all(row.deleted_at is not None for row in reminders)

    _, blocked = await create_simple_matter(h, "initial-delete-blocked", with_todo=True)
    blocked_id = blocked["matter"]["matter_id"]
    detail = await h.client.get(
        f"/api/v1/matters/{blocked_id}", headers=auth(h.token_a)
    )
    todo_id = detail.json()["todos"][0]["todo_id"]
    completed = await h.client.post(
        f"/api/v1/matters/{blocked_id}/todos/{todo_id}/complete",
        headers=auth(h.token_a),
    )
    assert completed.status_code == 200
    blocked_changes = await h.client.get(
        f"/api/v1/matters/{blocked_id}/changes", headers=auth(h.token_a)
    )
    assert blocked_changes.json()[0]["initial_delete_allowed"] is False
    assert blocked_changes.json()[0]["reason_code"] == "todo_changed"
    refused = await h.client.delete(
        f"/api/v1/matters/{blocked_id}/initial", headers=auth(h.token_a)
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "todo_changed"


@pytest.mark.asyncio
async def test_due_and_scheduled_reminders_acknowledge_once(harness):
    h = harness
    now = datetime.now(UTC)
    extraction = {
        "kind": "create",
        "title": "提醒状态事项",
        "todos": [
            {
                "title": "已经到点的提醒",
                "reminder_at": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "title": "未来提醒",
                "reminder_at": (now + timedelta(hours=2)).isoformat(),
            },
        ],
        "confidence": 0.9,
    }
    response = await intake(h, "reminder-states", "创建两个提醒", extraction)
    confirmed = await confirm(
        h, response.json()["draft"], "confirm-reminder-states"
    )
    assert confirmed.status_code == 200, confirmed.text
    by_title = {
        item["title"]: item for item in confirmed.json()["reminders"]
    }
    due = by_title["提醒：已经到点的提醒"]
    scheduled = by_title["提醒：未来提醒"]
    assert due["status"] == "due"
    assert scheduled["status"] == "scheduled"

    today = datetime.now(ZONE).date()
    today_body = await h.client.get(
        f"/api/v1/today?date={today.isoformat()}", headers=auth(h.token_a)
    )
    assert today_body.status_code == 200, today_body.text
    assert any(
        item["reminder_id"] == due["reminder_id"] and item["status"] == "due"
        for item in today_body.json()["reminders"]
    )

    first = await h.client.post(
        f"/api/v1/reminders/{due['reminder_id']}/acknowledge",
        headers=auth(h.token_a),
    )
    second = await h.client.post(
        f"/api/v1/reminders/{due['reminder_id']}/acknowledge",
        headers=auth(h.token_a),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "acknowledged"
    assert first.json()["acknowledged_at"] == second.json()["acknowledged_at"]

    due_list = await h.client.get(
        "/api/v1/reminders?status=due", headers=auth(h.token_a)
    )
    acknowledged_list = await h.client.get(
        "/api/v1/reminders?status=acknowledged", headers=auth(h.token_a)
    )
    assert all(item["reminder_id"] != due["reminder_id"] for item in due_list.json())
    assert any(
        item["reminder_id"] == due["reminder_id"]
        for item in acknowledged_list.json()
    )
    async with h.app.state.session_factory() as db:
        stored = await db.get(Reminder, due["reminder_id"])
        assert stored is not None
        assert stored.status == "completed"
        assert stored.acknowledged_at is not None


@pytest.mark.asyncio
async def test_completed_todo_cancels_only_its_related_future_reminder(harness):
    """One completed todo must not cancel reminders for unrelated todos."""

    h = harness
    now = datetime.now(UTC)
    extraction = {
        "kind": "create",
        "title": "合同推进",
        "todos": [
            {
                "title": "合同定稿",
                "reminder_at": (now + timedelta(hours=3)).isoformat(),
            },
            {
                "title": "发票确认",
                "reminder_at": (now + timedelta(hours=5)).isoformat(),
            },
        ],
        "confidence": 0.9,
    }
    created = await intake(h, "related-reminder-create", "创建合同待办", extraction)
    confirmed = await confirm(
        h, created.json()["draft"], "confirm-related-reminder-create"
    )
    matter_id = confirmed.json()["matter"]["matter_id"]
    todo_items = {
        item["title"]: item["todo_id"]
        for item in (
            await h.client.get(
                f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
            )
        ).json()["todos"]
    }

    update = {
        "kind": "update",
        "matter_id": matter_id,
        "changes_note": "合同已经定稿",
        "complete_todo_ids": [todo_items["合同定稿"]],
        "cancel_reminders": False,
        "confidence": 0.9,
    }
    preview = await intake(
        h,
        "related-reminder-update",
        "合同弄好了",
        update,
        matter_id=matter_id,
    )
    draft = preview.json()["draft"]
    assert [
        item["title"] for item in draft["payload"]["reminder_cancellations"]
    ] == ["提醒：合同定稿"]
    stopped = await confirm(h, draft, "confirm-related-reminder-update")
    assert stopped.status_code == 200, stopped.text

    detail = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
        )
    ).json()
    reminder_states = {item["title"]: item["status"] for item in detail["reminders"]}
    todo_states = {item["title"]: item["status"] for item in detail["todos"]}
    assert todo_states["合同定稿"] == "done"
    assert todo_states["发票确认"] == "open"
    assert reminder_states["提醒：合同定稿"] == "cancelled"
    assert reminder_states["提醒：发票确认"] == "scheduled"


@pytest.mark.asyncio
async def test_stop_following_previews_and_cancels_selected_future_items(harness):
    h = harness
    _, created = await create_simple_matter(h, "stop-following")
    matter_id = created["matter"]["matter_id"]
    future = datetime.now(UTC) + timedelta(days=5)
    due_at = datetime.now(UTC) - timedelta(hours=1)
    async with h.app.state.session_factory() as db:
        web_schedule = ScheduleEvent(
            user_id=h.user_a,
            matter_id=matter_id,
            title="未来应用内日程",
            starts_at=future,
            location="公开地点",
            status="scheduled",
            source="web_native",
            sync_status="local_only",
        )
        readonly_schedule = ScheduleEvent(
            user_id=h.user_a,
            matter_id=matter_id,
            title="手机只读日程",
            starts_at=future + timedelta(days=1),
            status="scheduled",
            source="ios_eventkit",
            sync_status="ios_synced",
        )
        db.add_all(
            [
                web_schedule,
                readonly_schedule,
                Reminder(
                    user_id=h.user_a,
                    matter_id=matter_id,
                    title="已经到点",
                    trigger_at=due_at,
                    status="pending",
                    source="web_native",
                ),
                Reminder(
                    user_id=h.user_a,
                    matter_id=matter_id,
                    title="未来催办一",
                    trigger_at=future,
                    status="pending",
                    source="web_native",
                ),
                Reminder(
                    user_id=h.user_a,
                    matter_id=matter_id,
                    title="未来催办二",
                    trigger_at=future + timedelta(hours=1),
                    status="pending",
                    source="web_native",
                ),
            ]
        )
        await db.commit()
        web_id = web_schedule.schedule_id
        readonly_id = readonly_schedule.schedule_id

    stop_body = {
        "kind": "update",
        "matter_id": matter_id,
        "changes_note": "项目暂停，不再提醒",
        "matter_action": "archive",
        "cancel_reminders": True,
        "confidence": 0.9,
    }
    preview = await intake(
        h,
        "stop-preview",
        "项目暂停，不再提醒",
        stop_body,
        matter_id=matter_id,
    )
    assert preview.status_code == 201, preview.text
    draft = preview.json()["draft"]
    schedule_options = {
        item["schedule_id"]: item
        for item in draft["payload"]["schedule_cancellations"]
    }
    assert schedule_options[web_id]["selected"] is True
    assert schedule_options[web_id]["cancellable"] is True
    assert schedule_options[readonly_id]["selected"] is False
    assert schedule_options[readonly_id]["cancellable"] is False
    assert len(draft["payload"]["reminder_cancellations"]) == 2

    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "cancel_schedule_ids": [web_id],
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    stopped = await confirm(
        h, patched.json(), "confirm-stop-preview"
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["cancelled_schedule_ids"] == [web_id]
    assert len(stopped.json()["cancelled_reminder_ids"]) == 2
    assert stopped.json()["matter"]["status"] == "archived"

    detail = await h.client.get(
        f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
    )
    assert [item["title"] for item in detail.json()["schedules"]] == ["手机只读日程"]
    reminder_states = {item["title"]: item["status"] for item in detail.json()["reminders"]}
    assert reminder_states["已经到点"] == "due"
    assert reminder_states["未来催办一"] == "cancelled"
    assert reminder_states["未来催办二"] == "cancelled"


def test_matter_plan_rejects_unstated_duration_and_workday_end():
    """Backend validation removes model-invented duration and 18:00 defaults."""

    target_day = datetime.now(ZONE).date() + timedelta(days=2)
    context = {
        "text": "周五下午两点开会，周四下班前准备合同",
        "now": datetime.now(UTC).isoformat(),
        "timezone": "Asia/Shanghai",
        "matter_ids": [],
        "todo_ids": [],
    }
    result = matter_plan.SPEC.validate_result(
        json.dumps(
            {
                "kind": "create",
                "title": "合成会议",
                "schedule": {
                    "title": "合成会议",
                    "starts_at": at(target_day, 14).isoformat(),
                    "ends_at": at(target_day, 15, 30).isoformat(),
                },
                "todos": [
                    {
                        "title": "准备合同",
                        "due_at": at(target_day - timedelta(days=1), 18).isoformat(),
                    }
                ],
                "confidence": 0.9,
            },
            ensure_ascii=False,
        ),
        context,
    )
    assert result["schedule"]["ends_at"] is None
    assert result["todos"][0]["due_at"] is None
    assert result["requires_clarification"] is True
    assert "下班前" in result["clarify"]

    ambiguous_context = {
        **context,
        "text": "周五下午两点开会，周四18点前交合同，提前两小时提醒我",
    }
    ambiguous = matter_plan.SPEC.validate_result(
        json.dumps(
            {
                "kind": "create",
                "title": "合成会议",
                "schedule": {
                    "title": "合成会议",
                    "starts_at": at(target_day, 14).isoformat(),
                },
                "todos": [
                    {
                        "title": "交合同",
                        "due_at": at(target_day - timedelta(days=1), 18).isoformat(),
                        "reminder_at": at(
                            target_day - timedelta(days=1), 16
                        ).isoformat(),
                    }
                ],
                "confidence": 0.9,
            },
            ensure_ascii=False,
        ),
        ambiguous_context,
    )
    assert ambiguous["requires_clarification"] is True
    assert "提醒对象" in ambiguous["clarify"]

    absence = matter_plan.SPEC.validate_result(
        json.dumps(
            {
                "intent": "question",
                "answer": "你这周末没有任何安排。",
            },
            ensure_ascii=False,
        ),
        context,
    )
    assert "不能确认" in absence["answer"]


@pytest.mark.asyncio
async def test_calendar_feed_adds_alarm_only_for_schedule_linked_reminder(harness):
    h = harness
    created = await h.client.post(
        "/api/v1/matters",
        json={"title": "日历提醒事项", "goal": ""},
        headers=auth(h.token_a),
    )
    matter_id = created.json()["matter_id"]
    starts_at = datetime.now(UTC) + timedelta(days=4)
    async with h.app.state.session_factory() as db:
        schedule = ScheduleEvent(
            user_id=h.user_a,
            matter_id=matter_id,
            title="带提醒日程",
            starts_at=starts_at,
            status="scheduled",
            source="web_native",
            sync_status="local_only",
        )
        db.add(schedule)
        await db.flush()
        db.add_all(
            [
                Reminder(
                    user_id=h.user_a,
                    matter_id=matter_id,
                    schedule_id=schedule.schedule_id,
                    title="会前提醒",
                    trigger_at=starts_at - timedelta(minutes=30),
                    status="pending",
                    source="web_native",
                ),
                Reminder(
                    user_id=h.user_a,
                    matter_id=matter_id,
                    title="独立待办提醒",
                    trigger_at=starts_at - timedelta(hours=1),
                    status="pending",
                    source="web_native",
                ),
            ]
        )
        await db.commit()

    subscription = await h.client.post(
        "/api/v1/calendar-subscription",
        json={"matter_ids": [matter_id]},
        headers=auth(h.token_a),
    )
    assert subscription.status_code == 201, subscription.text
    feed = await h.client.get(subscription.json()["feed_url"])
    assert feed.status_code == 200, feed.text
    calendar = Calendar.from_ical(feed.content)
    events = [item for item in calendar.walk() if item.name == "VEVENT"]
    assert len(events) == 1
    alarms = [item for item in events[0].walk() if item.name == "VALARM"]
    assert len(alarms) == 1
    assert str(alarms[0]["DESCRIPTION"]) == "会前提醒"
    assert alarms[0]["TRIGGER"].params["VALUE"] == "DATE-TIME"
    assert b"TRIGGER;VALUE=DATE-TIME:" in feed.content
