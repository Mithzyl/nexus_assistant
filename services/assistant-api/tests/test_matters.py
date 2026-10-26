"""Matters: natural intake, confirmation boundary, continuation, conflict, control.

All model calls go to the in-memory Nexus fake. Times are computed relative to
"now" in Asia/Shanghai, so the tests do not depend on a scripted calendar date.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from assistant_api.models import (
    Matter,
    MatterDraft,
    MatterTodo,
    Reminder,
    ScheduleEvent,
)
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def next_weekday(weekday: int) -> "datetime.date":
    today = datetime.now(ZONE).date()
    delta = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=delta)


def at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE)


def iso(value: datetime) -> str:
    return value.isoformat()


async def intake(harness, token: str, key: str, text: str, body: dict, matter_id: str | None = None):
    harness.fake.final_text = json.dumps(body, ensure_ascii=False)
    url = (
        f"/api/v1/matters/{matter_id}/intake"
        if matter_id
        else "/api/v1/matters/intake"
    )
    response = await harness.client.post(
        url,
        json={"text": text, "idempotency_key": key},
        headers=auth(token),
    )
    return response


def confirm(harness, token: str, draft: dict, key: str, **overrides):
    body = {
        "expected_version": draft["version"],
        "acknowledge_conflict": True,
        "create_schedule": True,
    }
    body.update(overrides)
    return harness.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",
        json=body,
        headers={**auth(token), "Idempotency-Key": key},
    )


async def counts(harness) -> dict:
    async with harness.app.state.session_factory() as db:
        return {
            "matters": len((await db.scalars(select(Matter))).all()),
            "drafts": len((await db.scalars(select(MatterDraft))).all()),
            "schedules": len((await db.scalars(select(ScheduleEvent))).all()),
            "todos": len((await db.scalars(select(MatterTodo))).all()),
            "reminders": len((await db.scalars(select(Reminder))).all()),
        }


@pytest.mark.asyncio
async def test_script_one_builds_a_matter_only_after_confirmation(harness):
    h = harness
    thursday = next_weekday(3)
    tuesday = thursday - timedelta(days=2)
    meeting_start = at(thursday, 15)
    meeting_end = at(thursday, 16)
    due = at(tuesday, 18)
    reminder_at = at(tuesday, 14)
    extraction = {
        "kind": "create",
        "title": "与林总洽谈年度合作合同",
        "goal": "敲定年度合作条款并拿到法务修订版",
        "changes_note": "下周四下午三点陆家嘴面谈，周二下班前拿到法务合同",
        "next_step": "周二 18:00 前催法务",
        "schedule": {
            "title": "与林总面谈",
            "starts_at": iso(meeting_start),
            "ends_at": iso(meeting_end),
            "location": "陆家嘴",
        },
        "todos": [
            {"title": "拿到法务合同修订版", "due_at": iso(due), "reminder_at": iso(reminder_at)}
        ],
        "confidence": 0.9,
    }
    baseline = await counts(h)
    response = await intake(h, h.token_a, "matter-intake-0001", "下周四下午三点要和 林总 谈合同", extraction)
    assert response.status_code == 201, response.text
    payload = response.json()
    draft = payload["draft"]
    assert draft["kind"] == "create"
    assert draft["status"] == "preview"
    assert draft["payload"]["title"] == "与林总洽谈年度合作合同"
    assert draft["payload"]["schedule"]["location"] == "陆家嘴"

    # Boundary: preview must not write any formal record.
    before = await counts(h)
    assert before["schedules"] == baseline["schedules"]
    assert before["reminders"] == baseline["reminders"]
    assert before["matters"] == baseline["matters"]

    confirmed = await confirm(h, h.token_a, draft, "confirm-key-0001")
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["matter"]["title"] == "与林总洽谈年度合作合同"
    assert result["schedule"]["matter_id"] == result["matter"]["matter_id"]
    assert len(result["reminders"]) == 1
    assert result["reminders"][0]["sync_status"] == "local_only"
    assert result["schedule"]["sync_status"] == "local_only"

    detail = await h.client.get(
        f"/api/v1/matters/{result['matter']['matter_id']}", headers=auth(h.token_a)
    )
    body = detail.json()
    assert body["status"] == "active"
    assert len(body["schedules"]) == 1
    assert len(body["todos"]) == 1
    assert body["progress"][0]["text"].startswith("下周四下午三点")

    # A repeated confirmation with the same key returns the same result.
    repeated = await confirm(h, h.token_a, draft, "confirm-key-0001")
    assert repeated.status_code == 200
    assert repeated.json()["replayed"] is True
    after = await counts(h)
    assert after["schedules"] == baseline["schedules"] + 1
    assert after["reminders"] == baseline["reminders"] + 1


@pytest.mark.asyncio
async def test_script_two_continues_the_matter_and_flags_the_conflict(harness):
    h = harness
    thursday = next_weekday(3)
    meeting_start = at(thursday, 15)
    extraction = {
        "kind": "create",
        "title": "与林总洽谈年度合作合同",
        "goal": "敲定年度合作条款",
        "schedule": {
            "title": "与林总面谈",
            "starts_at": iso(meeting_start),
            "ends_at": iso(at(thursday, 16)),
            "location": "陆家嘴",
        },
        "todos": [{"title": "拿到法务合同修订版"}],
        "confidence": 0.9,
    }
    created = await intake(h, h.token_a, "matter-intake-1001", "周五谈合同", extraction)
    draft = created.json()["draft"]
    confirmed = await confirm(h, h.token_a, draft, "confirm-key-1001")
    matter = confirmed.json()["matter"]
    detail = (
        await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    ).json()
    todo_id = detail["todos"][0]["todo_id"]

    # An existing appointment ends exactly when the proposed new time starts.
    async with h.app.state.session_factory() as db:
        db.add(
            ScheduleEvent(
                user_id=h.user_a,
                title="静安寺洗牙",
                starts_at=at(thursday, 13, 30).astimezone(UTC),
                ends_at=at(thursday, 14).astimezone(UTC),
                status="scheduled",
                source="web_native",
                sync_status="local_only",
            )
        )
        await db.commit()

    update = {
        "kind": "update",
        "matter_id": matter["matter_id"],
        "changes_note": "法务合同已发；林总改到周四下午两点",
        "schedule": {
            "title": "与林总面谈",
            "starts_at": iso(at(thursday, 14)),
            "ends_at": iso(at(thursday, 15)),
            "location": "陆家嘴",
        },
        "complete_todo_ids": [todo_id],
        "next_step": "确认改期后回复林总秘书",
        "confidence": 0.8,
    }
    continued = await intake(
        h, h.token_a, "matter-intake-1002", "合同发了，改到周四两点", update, matter_id=matter["matter_id"]
    )
    assert continued.status_code == 201, continued.text
    draft2 = continued.json()["draft"]
    assert draft2["kind"] == "update"
    assert draft2["matter_id"] == matter["matter_id"]
    assert draft2["conflicts"]["has_conflict"] is True
    assert len(draft2["conflicts"]["options"]) >= 2
    assert draft2["payload"]["schedule"]["schedule_id"] == detail["schedules"][0]["schedule_id"]

    # Unacknowledged conflict is refused by the server, not the page.
    blocked = await confirm(h, h.token_a, draft2, "confirm-key-1002", acknowledge_conflict=False)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conflict_detected"

    done = await confirm(h, h.token_a, draft2, "confirm-key-1002", acknowledge_conflict=True)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["schedule"]["starts_at"].startswith(at(thursday, 14).astimezone(UTC).strftime("%Y-%m-%dT%H"))
    detail_after = (
        await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    ).json()
    assert detail_after["todos"][0]["status"] == "done"
    assert detail_after["version"] == 2
    assert len(detail_after["schedules"]) == 1


@pytest.mark.asyncio
async def test_script_three_archives_and_deletes_without_leaving_context(harness):
    h = harness
    extraction = {
        "kind": "create",
        "title": "与林总洽谈年度合作合同",
        "goal": "敲定合作",
        "confidence": 0.9,
    }
    created = await intake(h, h.token_a, "matter-intake-2001", "谈合同", extraction)
    matter = (await confirm(h, h.token_a, created.json()["draft"], "confirm-key-2001")).json()["matter"]

    closing = {
        "kind": "update",
        "matter_id": matter["matter_id"],
        "changes_note": "对方预算冻结，这事暂停，不用再催我",
        "next_step": None,
        "confidence": 0.9,
    }
    draft = (
        await intake(h, h.token_a, "matter-intake-2002", "预算冻结，暂停", closing, matter_id=matter["matter_id"])
    ).json()["draft"]
    await confirm(h, h.token_a, draft, "confirm-key-2002")

    archived = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/archive",
        json={"expected_version": matter["version"] + 1, "cancel_reminders": True},
        headers=auth(h.token_a),
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"

    listing = await h.client.get("/api/v1/matters?group=active", headers=auth(h.token_a))
    assert listing.json()["items"] == []

    deleted = await h.client.delete(
        f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a)
    )
    assert deleted.status_code == 200
    assert (
        await h.client.get(
            f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a)
        )
    ).status_code == 404

    # A later intake must not see the deleted matter in its context.
    h.fake.calls.clear()
    fresh = {
        "kind": "create",
        "title": "新的合作沟通",
        "goal": "重新联系",
        "confidence": 0.8,
    }
    await intake(h, h.token_a, "matter-intake-2003", "再联系一下合作方", fresh)
    prompt_bodies = [
        json.loads(call["body"])
        for call in h.fake.calls
        if call.get("method") == "POST" and call.get("path") == "/api/v1/app/runs"
    ]
    joined = json.dumps(prompt_bodies, ensure_ascii=False)
    assert "与林总洽谈年度合作合同" not in joined


@pytest.mark.asyncio
async def test_ambiguous_match_requires_an_explicit_choice(harness):
    h = harness
    first = await h.client.post(
        "/api/v1/matters",
        json={"title": "合作合同沟通", "goal": "甲"},
        headers=auth(h.token_a),
    )
    second = await h.client.post(
        "/api/v1/matters",
        json={"title": "合作合同沟通（二期）", "goal": "乙"},
        headers=auth(h.token_a),
    )
    ids = [first.json()["matter_id"], second.json()["matter_id"]]

    extraction = {
        "kind": "update",
        "candidate_matter_ids": ids,
        "changes_note": "合同有新进展",
        "confidence": 0.6,
    }
    draft = (
        await intake(h, h.token_a, "matter-intake-3001", "合同有新进展", extraction)
    ).json()["draft"]
    assert draft["kind"] == "update"
    assert len(draft["candidates"]) == 2
    assert draft["payload"]["requires_choice"] is True

    no_choice = await confirm(h, h.token_a, draft, "confirm-key-3001")
    assert no_choice.status_code == 422
    assert no_choice.json()["error"]["code"] == "matter_choice_required"

    chosen = await confirm(
        h, h.token_a, draft, "confirm-key-3002", matter_id=ids[0], acknowledge_conflict=True
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["matter"]["matter_id"] == ids[0]
    untouched = (
        await h.client.get(f"/api/v1/matters/{ids[1]}", headers=auth(h.token_a))
    ).json()
    assert untouched["version"] == 1
    assert untouched["latest_progress"] is None


@pytest.mark.asyncio
async def test_stale_preview_and_changed_matter_are_rejected(harness):
    h = harness
    extraction = {
        "kind": "create",
        "title": "供应商续约",
        "goal": "续签",
        "confidence": 0.9,
    }
    draft = (await intake(h, h.token_a, "matter-intake-4001", "续约", extraction)).json()["draft"]
    stale_version = draft["version"]
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={"expected_version": stale_version, "title": "供应商年度续约"},
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200
    assert patched.json()["version"] == stale_version + 1

    old = await confirm(
        h, h.token_a, {"draft_id": draft["draft_id"], "version": stale_version}, "confirm-key-4001"
    )
    assert old.status_code == 409
    assert old.json()["error"]["code"] == "preview_stale"

    matter = (
        await confirm(h, h.token_a, patched.json(), "confirm-key-4002")
    ).json()["matter"]
    update = {
        "kind": "update",
        "matter_id": matter["matter_id"],
        "changes_note": "补充进展",
    }
    draft2 = (
        await intake(h, h.token_a, "matter-intake-4002", "补充", update, matter_id=matter["matter_id"])
    ).json()["draft"]
    edited = await h.client.patch(
        f"/api/v1/matters/{matter['matter_id']}",
        json={"expected_version": matter["version"], "title": "供应商年度续约（正式）"},
        headers=auth(h.token_a),
    )
    assert edited.status_code == 200
    stale_matter = await confirm(h, h.token_a, draft2, "confirm-key-4003")
    assert stale_matter.status_code == 409
    assert stale_matter.json()["error"]["code"] == "matter_changed"


@pytest.mark.asyncio
async def test_drafts_and_matters_are_account_scoped(harness):
    h = harness
    extraction = {"kind": "create", "title": "甲的私事", "goal": "x", "confidence": 0.9}
    draft = (await intake(h, h.token_a, "matter-intake-5001", "私事", extraction)).json()["draft"]
    assert (
        await h.client.get(
            f"/api/v1/matters/drafts/{draft['draft_id']}", headers=auth(h.token_b)
        )
    ).status_code == 404
    assert (
        await confirm(h, h.token_b, draft, "confirm-key-5001")
    ).status_code == 404

@pytest.mark.asyncio
async def test_naive_model_time_is_anchored_in_the_user_timezone(harness):
    from assistant_api.services import matter_plan

    context = {
        "text": "下周四下午三点开会",
        "now": "2026-10-26T09:00:00+00:00",
        "timezone": "Asia/Shanghai",
        "matter_ids": [],
        "todo_ids": [],
    }
    result = matter_plan.validate_result(
        '{"kind":"create","title":"开会","schedule":{"starts_at":"2026-11-05T15:00:00"}}',
        context,
    )
    assert result["schedule"]["starts_at"].isoformat() == "2026-11-05T07:00:00+00:00"


def test_model_output_cannot_touch_another_users_matter():
    from assistant_api.services import ai_tasks, matter_plan

    context = {
        "text": "更新一下",
        "now": "2026-10-26T09:00:00+00:00",
        "timezone": "Asia/Shanghai",
        "matter_ids": [],
        "todo_ids": [],
    }
    with pytest.raises(ai_tasks.AiTaskResultRejected) as failure:
        matter_plan.validate_result(
            '{"kind":"update","matter_id":"someone-else"}', context
        )
    assert failure.value.reason == "unknown_matter"
