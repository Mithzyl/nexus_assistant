"""Today enrichment, direct edit drafts and change history."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from assistant_api.models import Matter, MatterDraft, NexusRun, Reminder, ScheduleEvent
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")
DAY = datetime.now(ZONE).date() + timedelta(days=1)


def at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


async def intake(harness, key: str, text: str, body: dict, matter_id: str | None = None):
    harness.fake.final_text = json.dumps(body, ensure_ascii=False)
    url = f"/api/v1/matters/{matter_id}/intake" if matter_id else "/api/v1/matters/intake"
    return await harness.client.post(
        url, json={"text": text, "idempotency_key": key}, headers=auth(harness.token_a)
    )


async def confirm(harness, draft: dict, key: str, **overrides):
    payload = {"expected_version": draft["version"], "acknowledge_conflict": True}
    payload.update(overrides)
    return await harness.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
        json=payload,
        headers={**auth(harness.token_a), "Idempotency-Key": key},
    )


async def create_matter(harness, day, *, title="今天事项", reminder: bool = True):
    extraction = {
        "kind": "create",
        "title": title,
        "goal": "g",
        "schedule": {
            "title": "面谈",
            "starts_at": at(day, 15).isoformat(),
            "ends_at": at(day, 16).isoformat(),
            "location": "陆家嘴",
        },
        "todos": [
            {
                "title": "准备材料",
                "due_at": at(day, 12).isoformat(),
                "reminder_at": at(day, 9).isoformat() if reminder else None,
            }
        ],
        "confidence": 0.9,
    }
    draft = (await intake(harness, "key-create-fixed-01", "创建", extraction)).json()["draft"]
    return (await confirm(harness, draft, "confirm-create-fixed-01")).json()


@pytest.mark.asyncio
async def test_today_associates_matters_deadlines_drafts_and_route_state(harness):
    h = harness
    result = await create_matter(h, DAY)
    matter_id = result["matter"]["matter_id"]

    update = {
        "kind": "update",
        "matter_id": matter_id,
        "changes_note": "改到下午四点",
        "schedule": {
            "title": "面谈",
            "starts_at": at(DAY, 16).isoformat(),
            "ends_at": at(DAY, 17).isoformat(),
            "location": "陆家嘴",
        },
        "confidence": 0.9,
    }
    draft = (
        await intake(h, "key-today-update-01", "改到四点", update, matter_id=matter_id)
    ).json()["draft"]

    today = (await h.client.get(f"/api/v1/today?date={DAY.isoformat()}", headers=auth(h.token_a))).json()
    schedule = today["schedules"][0]
    assert schedule["matter_id"] == matter_id
    assert schedule["matter_title"] == "今天事项"
    assert schedule["route"]["status"] == "unverified"
    assert schedule["route"]["reason_code"] == "no_linked_route_result"
    assert today["reminders"][0]["matter_title"] == "今天事项"
    assert today["deadlines"][0]["title"] == "准备材料"
    assert today["deadlines"][0]["matter_id"] == matter_id
    assert today["pending_drafts"] == 1
    item = today["drafts"][0]
    assert item["draft_id"] == draft["draft_id"]
    assert item["matter_title"] == "今天事项"
    assert "conflict" in item

    confirmed = await confirm(h, draft, "confirm-today-update-01", acknowledge_conflict=True)
    assert confirmed.status_code == 200, confirmed.text
    after = (await h.client.get(f"/api/v1/today?date={DAY.isoformat()}", headers=auth(h.token_a))).json()
    assert after["pending_drafts"] == 0
    assert after["drafts"] == []
    assert [row["starts_at"] for row in after["schedules"]][0].startswith(
        at(DAY, 16).strftime("%Y-%m-%dT%H:%M")
    )


@pytest.mark.asyncio
async def test_direct_edit_schedule_preview_then_confirm(harness):
    h = harness
    created = await create_matter(h, DAY, title="直接编辑")
    matter_id = created["matter"]["matter_id"]
    detail = (await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))).json()
    schedule_id = detail["schedules"][0]["schedule_id"]

    preview = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"idempotency_key": "direct-edit-0001", "schedule_id": schedule_id},
        headers=auth(h.token_a),
    )
    assert preview.status_code == 201, preview.text
    draft = preview.json()
    assert draft["payload"]["schedule"]["schedule_id"] == schedule_id
    assert draft["payload"]["title"] is None
    # Nothing formal changes before confirmation.
    detail_before = (await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))).json()
    assert detail_before["schedules"][0]["starts_at"].startswith(at(DAY, 15).strftime("%Y-%m-%dT%H:%M"))

    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "schedule": {
                "schedule_id": schedule_id,
                "title": "面谈（改）",
                "starts_at": at(DAY, 17).isoformat(),
                "ends_at": at(DAY, 18).isoformat(),
                "location": "新地点",
            },
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    done = await confirm(h, patched.json(), "confirm-direct-edit-01")
    assert done.status_code == 200, done.text
    detail_after = (await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))).json()
    assert len(detail_after["schedules"]) == 1
    assert detail_after["schedules"][0]["location"] == "新地点"
    assert detail_after["schedules"][0]["title"] == "面谈（改）"

    again = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"idempotency_key": "direct-edit-0001", "schedule_id": schedule_id},
        headers=auth(h.token_a),
    )
    assert again.status_code == 201
    assert again.json()["draft_id"] == draft["draft_id"]


@pytest.mark.asyncio
async def test_direct_edit_todo_deadline_and_reminder(harness):
    h = harness
    created = await create_matter(h, DAY, title="待办时间")
    matter_id = created["matter"]["matter_id"]
    detail = (await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))).json()
    todo = detail["todos"][0]

    preview = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"idempotency_key": "direct-todo-0001", "todo_id": todo["todo_id"]},
        headers=auth(h.token_a),
    )
    assert preview.status_code == 201, preview.text
    draft = preview.json()
    assert draft["payload"]["todos"][0]["todo_id"] == todo["todo_id"]
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "todos": [
                {
                    "todo_id": todo["todo_id"],
                    "title": todo["title"],
                    "due_at": at(DAY, 18).isoformat(),
                    "reminder_at": at(DAY, 17).isoformat(),
                }
            ],
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    done = await confirm(h, patched.json(), "confirm-direct-todo-01")
    assert done.status_code == 200, done.text
    detail_after = (await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))).json()
    assert detail_after["todos"][0]["due_at"].startswith(at(DAY, 18).strftime("%Y-%m-%dT%H:%M"))
    async with h.app.state.session_factory() as db:
        reminders = (await db.scalars(select(Reminder).where(Reminder.todo_id == todo["todo_id"]))).all()
        assert len(reminders) == 1
        assert reminders[0].trigger_at is not None


@pytest.mark.asyncio
async def test_direct_edit_rejects_imported_schedule(harness):
    h = harness
    created = await create_matter(h, DAY, title="导入拒绝")
    matter_id = created["matter"]["matter_id"]
    async with h.app.state.session_factory() as db:
        event = ScheduleEvent(
            user_id=h.user_a,
            matter_id=matter_id,
            title="苹果日程",
            starts_at=at(DAY, 10),
            ends_at=at(DAY, 11),
            status="scheduled",
            source="ios_eventkit",
            sync_status="ios_synced",
            source_external_id="evt-direct-1",
        )
        db.add(event)
        await db.commit()
        await db.refresh(event)
        event_id = event.schedule_id
    response = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"idempotency_key": "direct-imported-01", "schedule_id": event_id},
        headers=auth(h.token_a),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schedule_not_editable"


@pytest.mark.asyncio
async def test_changes_list_explains_revert_eligibility(harness):
    h = harness
    created = await create_matter(h, DAY, title="变更历史")
    matter_id = created["matter"]["matter_id"]
    first_change = created["change_id"]
    update = {"kind": "update", "matter_id": matter_id, "changes_note": "第二次", "confidence": 0.9}
    draft = (
        await intake(h, "key-changes-02", "第二次", update, matter_id=matter_id)
    ).json()["draft"]
    second = (await confirm(h, draft, "confirm-changes-02")).json()

    listing = (
        await h.client.get(f"/api/v1/matters/{matter_id}/changes", headers=auth(h.token_a))
    ).json()
    by_id = {item["change_id"]: item for item in listing}
    assert by_id[second["change_id"]]["revertable"] is True
    assert by_id[first_change]["revertable"] is False
    assert by_id[first_change]["reason_code"] == "later_changes"
    assert by_id[first_change]["before"] and by_id[first_change]["after"]

    reverted = await h.client.post(
        f"/api/v1/matters/{matter_id}/changes/{second['change_id']}/revert",
        json={"expected_version": second["matter"]["version"]},
        headers=auth(h.token_a),
    )
    assert reverted.status_code == 200, reverted.text
    after = (
        await h.client.get(f"/api/v1/matters/{matter_id}/changes", headers=auth(h.token_a))
    ).json()
    item = next(row for row in after if row["change_id"] == second["change_id"])
    assert item["revertable"] is False
    assert item["reason_code"] == "already_reverted"


@pytest.mark.asyncio
async def test_question_intent_returns_answer_without_second_run(harness):
    h = harness
    h.fake.final_text = json.dumps(
        {"intent": "question", "answer": "今天多云，气温 18 到 24 度。"}, ensure_ascii=False
    )
    response = await h.client.post(
        "/api/v1/matters/intake",
        json={"text": "今天天气怎么样？", "idempotency_key": "question-answer-01"},
        headers=auth(h.token_a),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft"] is None
    assert body["intent"] == "question"
    assert body["answer"] == "今天多云，气温 18 到 24 度。"
    async with h.app.state.session_factory() as db:
        runs = (await db.scalars(select(NexusRun).where(NexusRun.purpose == "matter_plan"))).all()
        assert len(runs) == 1
        assert len((await db.scalars(select(Matter))).all()) == 0
