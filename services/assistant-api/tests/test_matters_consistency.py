"""Targeted tests for this round's continuity/consistency changes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from assistant_api.models import Matter, MatterDraft, MatterTodo, ScheduleEvent
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def day_after(days: int = 2):
    return datetime.now(ZONE).date() + timedelta(days=days)


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


@pytest.mark.asyncio
async def test_question_intent_returns_no_draft_and_unclear_is_rejected(harness):
    h = harness
    async with h.app.state.session_factory() as db:
        before = len((await db.scalars(select(Matter))).all())

    question = await intake(h, "key-question-01", "今天天气怎么样？", {"intent": "question"})
    assert question.status_code == 200, question.text
    body = question.json()
    assert body["intent"] == "question"
    assert body["draft"] is None
    assert body["message"]

    unclear = await intake(h, "key-unclear-01", "那个事", {"intent": "unclear"})
    assert unclear.status_code == 422
    assert unclear.json()["error"]["code"] == "matter_intent_unclear"

    async with h.app.state.session_factory() as db:
        assert len((await db.scalars(select(Matter))).all()) == before
        drafts = (await db.scalars(select(MatterDraft))).all()
        assert drafts == []


@pytest.mark.asyncio
async def test_minimal_flow_edit_confirm_read_then_delete(harness):
    h = harness
    day = day_after()
    extraction = {
        "kind": "create",
        "title": "全流程事项",
        "goal": "走通最小链路",
        "schedule": {
            "title": "面谈",
            "starts_at": at(day, 10).isoformat(),
            "ends_at": at(day, 11).isoformat(),
            "location": "旧地点",
        },
        "todos": [{"title": "准备材料"}],
        "confidence": 0.9,
    }
    draft = (await intake(h, "key-flow-01", "周五十点面谈", extraction)).json()["draft"]
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "schedule": {
                "schedule_id": draft["payload"]["schedule"].get("schedule_id"),
                "title": "面谈（改）",
                "starts_at": at(day, 14).isoformat(),
                "ends_at": at(day, 15).isoformat(),
                "location": "新地点",
            },
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["payload"]["schedule"]["location"] == "新地点"

    done = await confirm(h, patched.json(), "confirm-flow-01")
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["applied"] is True
    assert result["change"]["after"]["schedule"]["location"] == "新地点"
    assert result["conflict"]["has_conflict"] is False
    matter_id = result["matter"]["matter_id"]

    detail = (await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))).json()
    assert detail["schedules"][0]["location"] == "新地点"
    today = (await h.client.get(f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a))).json()
    assert [item["location"] for item in today["schedules"]] == ["新地点"]

    deleted = await h.client.delete(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))
    assert deleted.status_code == 200
    today_after = (await h.client.get(f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a))).json()
    assert today_after["schedules"] == []


@pytest.mark.asyncio
async def test_editing_preview_invalidates_the_selected_option(harness):
    h = harness
    day = day_after(4)
    base = {
        "kind": "create",
        "title": "方案失效",
        "schedule": {
            "title": "面谈",
            "starts_at": at(day, 15).isoformat(),
            "ends_at": at(day, 16).isoformat(),
        },
        "confidence": 0.9,
    }
    matter = (await confirm(h, (await intake(h, "key-opt-01", "创建", base)).json()["draft"], "confirm-opt-01")).json()["matter"]
    async with h.app.state.session_factory() as db:
        db.add(
            ScheduleEvent(
                user_id=h.user_a,
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
            "title": "面谈",
            "starts_at": at(day, 14).isoformat(),
            "ends_at": at(day, 15).isoformat(),
        },
        "confidence": 0.9,
    }
    draft = (
        await intake(h, "key-opt-02", "改到两点", update, matter_id=matter["matter_id"])
    ).json()["draft"]
    assert draft["conflicts"]["has_conflict"] is True
    applied = await h.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/apply-option",
        json={"expected_version": draft["version"], "option_id": "A"},
        headers=auth(h.token_a),
    )
    assert applied.status_code == 200, applied.text
    edited = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={"expected_version": applied.json()["version"], "title": "面谈（再改）"},
        headers=auth(h.token_a),
    )
    assert edited.status_code == 200
    stale_option = await confirm(h, edited.json(), "confirm-opt-02", option_id="A")
    assert stale_option.status_code == 409
    assert stale_option.json()["error"]["code"] == "option_not_selected"
    ok = await confirm(h, edited.json(), "confirm-opt-03")
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_revert_does_not_reopen_a_todo_completed_later(harness):
    h = harness
    extraction = {
        "kind": "create",
        "title": "撤回保护待办",
        "goal": "x",
        "todos": [{"title": "提交材料"}],
        "confidence": 0.9,
    }
    first = (await confirm(h, (await intake(h, "key-rev-01", "创建", extraction)).json()["draft"], "confirm-rev-01")).json()
    matter = first["matter"]
    detail = (await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))).json()
    todo_id = detail["todos"][0]["todo_id"]
    done = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/todos/{todo_id}/complete",
        headers=auth(h.token_a),
    )
    assert done.status_code == 200

    reverted = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/changes/{first['change_id']}/revert",
        json={"expected_version": matter["version"]},
        headers=auth(h.token_a),
    )
    assert reverted.status_code == 200, reverted.text
    detail_after = (await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))).json()
    assert detail_after["todos"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_drafts_of_a_deleted_matter_are_not_readable(harness):
    h = harness
    extraction = {"kind": "create", "title": "将被删除", "confidence": 0.9}
    matter = (await confirm(h, (await intake(h, "key-del-01", "创建", extraction)).json()["draft"], "confirm-del-01")).json()["matter"]
    update = {"kind": "update", "matter_id": matter["matter_id"], "changes_note": "补充", "confidence": 0.9}
    draft = (
        await intake(h, "key-del-02", "补充", update, matter_id=matter["matter_id"])
    ).json()["draft"]
    assert (
        await h.client.get(f"/api/v1/matters/drafts/{draft['draft_id']}", headers=auth(h.token_a))
    ).status_code == 200
    await h.client.delete(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    hidden = await h.client.get(f"/api/v1/matters/drafts/{draft['draft_id']}", headers=auth(h.token_a))
    assert hidden.status_code == 404
    listing = await h.client.get("/api/v1/matters/drafts?status=preview", headers=auth(h.token_a))
    assert all(item["draft_id"] != draft["draft_id"] for item in listing.json())
