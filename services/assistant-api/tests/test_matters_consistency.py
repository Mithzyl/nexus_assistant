"""Targeted tests for this round's continuity/consistency changes."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from assistant_api.models import Matter, MatterDraft, MatterTodo, NexusRun, ScheduleEvent
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def day_after(days: int = 2):
    return datetime.now(ZONE).date() + timedelta(days=days)


def at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


async def intake(
    harness,
    key: str,
    text: str,
    body: dict,
    matter_id: str | None = None,
    continuation_run_id: str | None = None,
):
    harness.fake.final_text = json.dumps(body, ensure_ascii=False)
    url = f"/api/v1/matters/{matter_id}/intake" if matter_id else "/api/v1/matters/intake"
    return await harness.client.post(
        url,
        json={
            "text": text,
            "idempotency_key": key,
            "continuation_run_id": continuation_run_id,
        },
        headers=auth(harness.token_a),
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
async def test_question_and_unclear_results_are_durable_and_scoped(harness):
    h = harness
    async with h.app.state.session_factory() as db:
        before = len((await db.scalars(select(Matter))).all())

    question = await intake(h, "key-question-01", "今天天气怎么样？", {"intent": "question"})
    assert question.status_code == 200, question.text
    body = question.json()
    assert body["intent"] == "question"
    assert body["outcome"] == "answer"
    assert body["draft"] is None
    assert body["message"]
    question_id = body["local_run_id"]

    replayed_question = await intake(
        h, "key-question-01", "今天天气怎么样？", {"intent": "question"}
    )
    assert replayed_question.status_code == 200
    assert replayed_question.json()["local_run_id"] == question_id
    assert replayed_question.json()["replayed"] is True

    unclear = await intake(h, "key-unclear-01", "那个事", {"intent": "unclear"})
    assert unclear.status_code == 200, unclear.text
    assert unclear.json()["outcome"] == "needs_clarification"
    assert unclear.json()["clarify"]
    unclear_id = unclear.json()["local_run_id"]

    restored_answer = await h.client.get(
        f"/api/v1/matters/intake/{question_id}", headers=auth(h.token_a)
    )
    assert restored_answer.status_code == 200
    assert restored_answer.json()["outcome"] == "answer"
    restored_clarify = await h.client.get(
        f"/api/v1/matters/intake/{unclear_id}", headers=auth(h.token_a)
    )
    assert restored_clarify.status_code == 200
    assert restored_clarify.json()["outcome"] == "needs_clarification"
    assert (
        await h.client.get(
            f"/api/v1/matters/intake/{question_id}", headers=auth(h.token_b)
        )
    ).status_code == 404
    history = await h.client.get(
        "/api/v1/matters/intake", headers=auth(h.token_a)
    )
    assert {item["local_run_id"] for item in history.json()} == {
        question_id,
        unclear_id,
    }
    assert (
        await h.client.get("/api/v1/matters/intake", headers=auth(h.token_b))
    ).json() == []

    async with h.app.state.session_factory() as db:
        assert len((await db.scalars(select(Matter))).all()) == before
        drafts = (await db.scalars(select(MatterDraft))).all()
        assert drafts == []


@pytest.mark.asyncio
async def test_clarification_continuation_is_linked_without_replaying_original(harness):
    h = harness
    day = day_after(3)
    clarification = {
        "intent": "unclear",
        "clarify": "还需要补充具体时间。",
        "clarification": {
            "missing_information": ["周四下午的开始时间"],
            "pending": {
                "title": "客户面谈",
                "goal": "确认合作",
                "schedule": {"location": "陆家嘴"},
                "todos": [],
            },
        },
    }
    first = await intake(h, "followup-parent-01", "周四约客户面谈", clarification)
    assert first.status_code == 200, first.text
    parent_id = first.json()["local_run_id"]
    assert first.json()["outcome"] == "needs_clarification"

    continuation = {
        "kind": "create",
        "title": "客户面谈",
        "goal": "确认合作",
        "schedule": {
            "title": "客户面谈",
            "starts_at": at(day, 14).isoformat(),
            "ends_at": at(day, 15).isoformat(),
            "location": "陆家嘴",
        },
        "confidence": 0.9,
    }
    second = await intake(
        h,
        "followup-child-01",
        "周四下午两点",
        continuation,
        continuation_run_id=parent_id,
    )
    assert second.status_code == 201, second.text
    child = second.json()
    assert child["outcome"] == "draft"
    assert child["parent_run_id"] == parent_id
    assert child["draft"]["payload"]["title"] == "客户面谈"
    child_model_call = [
        call for call in h.fake.calls if call["path"] == "/api/v1/app/runs"
    ][-1]
    assert "周四约客户面谈" not in child_model_call["body"]

    foreign = await h.client.post(
        "/api/v1/matters/intake",
        json={
            "text": "周四下午两点",
            "idempotency_key": "followup-foreign-01",
            "continuation_run_id": parent_id,
        },
        headers=auth(h.token_b),
    )
    assert foreign.status_code == 404

    # Retrying the original operation returns the same clarification instead of
    # replaying the new child result or creating a second draft.
    retried = await intake(h, "followup-parent-01", "周四约客户面谈", continuation)
    assert retried.status_code == 200
    assert retried.json()["local_run_id"] == parent_id
    assert retried.json()["outcome"] == "needs_clarification"
    async with h.app.state.session_factory() as db:
        runs = (
            await db.scalars(select(NexusRun).where(NexusRun.purpose == "matter_plan"))
        ).all()
        assert len(runs) == 2
        drafts = (await db.scalars(select(MatterDraft))).all()
        assert len(drafts) == 1


@pytest.mark.asyncio
async def test_failed_continuation_keeps_parent_and_success_replaces_it_atomically(harness):
    h = harness
    day = day_after(6)
    parent_body = {
        "kind": "create",
        "title": "可恢复预览",
        "goal": "先保留原安排",
        "schedule": {
            "title": "原安排",
            "starts_at": at(day, 10).isoformat(),
            "ends_at": at(day, 11).isoformat(),
        },
        "confidence": 0.9,
    }
    parent_response = await intake(h, "replace-parent-01", "创建", parent_body)
    parent_draft = parent_response.json()["draft"]
    parent_run_id = parent_response.json()["local_run_id"]

    h.fake.final_text = "not valid json"
    failed = await h.client.post(
        "/api/v1/matters/intake",
        json={
            "text": "改到下午三点",
            "idempotency_key": "replace-child-fail",
            "continuation_run_id": parent_run_id,
        },
        headers=auth(h.token_a),
    )
    assert failed.status_code == 422
    original = await h.client.get(
        f"/api/v1/matters/drafts/{parent_draft['draft_id']}",
        headers=auth(h.token_a),
    )
    assert original.status_code == 200
    assert original.json()["status"] == "preview"

    child_body = {
        "kind": "create",
        "title": "可恢复预览",
        "goal": "先保留原安排",
        "schedule": {
            "title": "原安排",
            "starts_at": at(day, 15).isoformat(),
            "ends_at": at(day, 16).isoformat(),
        },
        "confidence": 0.9,
    }
    child_response = await intake(
        h,
        "replace-child-01",
        "改到下午三点",
        child_body,
        continuation_run_id=parent_run_id,
    )
    assert child_response.status_code == 201, child_response.text
    child_draft = child_response.json()["draft"]
    assert child_draft["draft_id"] != parent_draft["draft_id"]

    parent_after = await h.client.get(
        f"/api/v1/matters/drafts/{parent_draft['draft_id']}",
        headers=auth(h.token_a),
    )
    assert parent_after.json()["status"] == "cancelled"
    previews = await h.client.get(
        "/api/v1/matters/drafts?status=preview", headers=auth(h.token_a)
    )
    assert [item["draft_id"] for item in previews.json()] == [child_draft["draft_id"]]

    replayed = await intake(
        h,
        "replace-child-01",
        "改到下午三点",
        child_body,
        continuation_run_id=parent_run_id,
    )
    assert replayed.status_code == 201
    assert replayed.json()["draft"]["draft_id"] == child_draft["draft_id"]

    cannot_confirm_parent = await confirm(
        h, parent_after.json(), "replace-parent-confirm"
    )
    assert cannot_confirm_parent.status_code == 409


@pytest.mark.asyncio
async def test_stop_following_only_archives_and_cancels_reminders_after_confirmation(harness):
    h = harness
    day = day_after(4)
    extraction = {
        "kind": "create",
        "title": "预算冻结事项",
        "goal": "等待预算恢复",
        "todos": [
            {
                "title": "继续催预算",
                "due_at": at(day, 10).isoformat(),
                "reminder_at": at(day, 9).isoformat(),
            }
        ],
        "confidence": 0.9,
    }
    created = await confirm(
        h,
        (await intake(h, "stop-parent-01", "创建", extraction)).json()["draft"],
        "stop-confirm-create-01",
    )
    confirmed = created.json()
    matter_id = confirmed["matter"]["matter_id"]

    stop_body = {
        "kind": "update",
        "matter_id": matter_id,
        "changes_note": "对方预算冻结，这事暂停，不用再催我。",
        "matter_action": "archive",
        "cancel_reminders": True,
        "confidence": 0.9,
    }
    draft = (
        await intake(
            h,
            "stop-preview-01",
            "对方预算冻结，这事暂停，不用再催我。",
            stop_body,
            matter_id=matter_id,
        )
    ).json()["draft"]
    action_kinds = {item["kind"] for item in draft["payload"]["action_plan"]}
    assert {"update_matter_status", "cancel_reminder"} <= action_kinds

    before = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
        )
    ).json()
    assert before["status"] == "active"
    assert before["reminders"][0]["status"] == "pending"

    stopped = await confirm(h, draft, "stop-confirm-01")
    assert stopped.status_code == 200, stopped.text
    assert len(stopped.json()["cancelled_reminder_ids"]) == 1
    assert stopped.json()["notifications_retracted"] is False
    after = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
        )
    ).json()
    assert after["status"] == "archived"
    assert after["reminders"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_archive_without_explicit_stop_request_keeps_pending_reminders(harness):
    h = harness
    day = day_after(5)
    extraction = {
        "kind": "create",
        "title": "仅归档事项",
        "todos": [
            {
                "title": "后续提醒",
                "due_at": at(day, 10).isoformat(),
                "reminder_at": at(day, 9).isoformat(),
            }
        ],
        "confidence": 0.9,
    }
    confirmed = (
        await confirm(
            h,
            (await intake(h, "archive-only-01", "创建", extraction)).json()["draft"],
            "archive-only-confirm-create",
        )
    ).json()
    matter_id = confirmed["matter"]["matter_id"]
    draft = (
        await intake(
            h,
            "archive-only-02",
            "先归档，提醒以后再说。",
            {
                "kind": "update",
                "matter_id": matter_id,
                "changes_note": "先归档",
                "matter_action": "archive",
                "cancel_reminders": False,
                "confidence": 0.9,
            },
            matter_id=matter_id,
        )
    ).json()["draft"]
    done = await confirm(h, draft, "archive-only-confirm")
    assert done.status_code == 200, done.text
    detail = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
        )
    ).json()
    assert detail["status"] == "archived"
    assert detail["reminders"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_long_intake_keeps_existing_context_and_multiple_time_actions(harness):
    h = harness
    day = day_after(7)
    async with h.app.state.session_factory() as db:
        await db.execute(
            delete(ScheduleEvent).where(ScheduleEvent.user_id == h.user_a)
        )
        dentist = ScheduleEvent(
            user_id=h.user_a,
            title="牙医洗牙",
            starts_at=at(day, 13),
            ends_at=at(day, 13, 30),
            location="静安寺",
            status="scheduled",
            source="web_native",
            sync_status="local_only",
        )
        db.add(dentist)
        await db.commit()
        dentist_id = dentist.schedule_id

    extraction = {
        "kind": "create",
        "title": "与林总洽谈年度合作合同",
        "goal": "确认合作条款",
        "schedule": {
            "title": "与林总面谈",
            "starts_at": at(day, 15).isoformat(),
            "ends_at": at(day, 16).isoformat(),
            "location": "陆家嘴",
        },
        "related_schedules": [
            {
                "title": "牙医洗牙",
                "starts_at": at(day, 13).isoformat(),
                "ends_at": at(day, 13, 30).isoformat(),
                "location": "静安寺",
            },
            {
                "title": "先去前台取访客证",
                "starts_at": at(day, 12).isoformat(),
                "ends_at": at(day, 12, 15).isoformat(),
                "location": "陆家嘴",
            },
        ],
        "todos": [
            {
                "title": "拿到法务合同修订版",
                "due_at": at(day - timedelta(days=2), 18).isoformat(),
                "reminder_at": at(day - timedelta(days=2), 14).isoformat(),
            }
        ],
        "confidence": 0.9,
    }
    text = (
        "下周四下午三点去陆家嘴和林总谈合作。周二下班前让法务发合同，"
        "那天中午约了牙医洗牙，一点半结束。"
    )
    response = await intake(h, "long-context-01", text, extraction)
    assert response.status_code == 201, response.text
    draft = response.json()["draft"]
    related = draft["payload"]["related_schedules"]
    assert {item["kind"] for item in related} == {"existing", "unverified"}
    existing = next(item for item in related if item["kind"] == "existing")
    assert existing["schedule_id"] == dentist_id
    action_kinds = {item["kind"] for item in draft["payload"]["action_plan"]}
    assert {"create_matter", "create_schedule", "create_todo", "context_only"} <= (
        action_kinds
    )

    async with h.app.state.session_factory() as db:
        assert len(
            (
                await db.scalars(
                    select(ScheduleEvent).where(ScheduleEvent.user_id == h.user_a)
                )
            ).all()
        ) == 1
        assert (
            await db.scalars(select(MatterTodo).where(MatterTodo.user_id == h.user_a))
        ).all() == []

    done = await confirm(h, draft, "long-context-confirm-01")
    assert done.status_code == 200, done.text
    async with h.app.state.session_factory() as db:
        schedules = (
            await db.scalars(
                select(ScheduleEvent).where(ScheduleEvent.user_id == h.user_a)
            )
        ).all()
        assert len(schedules) == 2
        todos = (
            await db.scalars(
                select(MatterTodo).where(MatterTodo.user_id == h.user_a)
            )
        ).all()
        assert [todo.title for todo in todos] == ["拿到法务合同修订版"]


@pytest.mark.asyncio
async def test_ambiguous_target_schedule_requires_choice_instead_of_overwriting_first(harness):
    h = harness
    day = day_after(8)
    matter = (
        await h.client.post(
            "/api/v1/matters",
            json={"title": "多场会议"},
            headers=auth(h.token_a),
        )
    ).json()
    async with h.app.state.session_factory() as db:
        db.add_all(
            [
                ScheduleEvent(
                    user_id=h.user_a,
                    matter_id=matter["matter_id"],
                    title="客户面谈",
                    starts_at=at(day, 10),
                    ends_at=at(day, 11),
                    status="scheduled",
                    source="web_native",
                    sync_status="local_only",
                ),
                ScheduleEvent(
                    user_id=h.user_a,
                    matter_id=matter["matter_id"],
                    title="内部面谈",
                    starts_at=at(day, 14),
                    ends_at=at(day, 15),
                    status="scheduled",
                    source="web_native",
                    sync_status="local_only",
                ),
            ]
        )
        await db.commit()
    response = await intake(
        h,
        "ambiguous-schedule-01",
        "面谈改到下午三点",
        {
            "kind": "update",
            "matter_id": matter["matter_id"],
            "changes_note": "面谈改到下午三点",
            "schedule": {
                "title": "面谈",
                "starts_at": at(day, 15).isoformat(),
                "ends_at": at(day, 16).isoformat(),
            },
            "confidence": 0.9,
        },
        matter_id=matter["matter_id"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "needs_clarification"
    restored = await h.client.get(
        f"/api/v1/matters/intake/{body['local_run_id']}", headers=auth(h.token_a)
    )
    candidates = restored.json()["clarify"]
    assert "哪一条" in candidates
    assert len(restored.json()["pending_context"]["schedule_candidates"]) == 2
    assert (
        await h.client.get(
            "/api/v1/matters/drafts?status=preview", headers=auth(h.token_a)
        )
    ).json() == []


@pytest.mark.asyncio
async def test_deleted_parent_schedule_cannot_reenter_through_continuation(harness):
    h = harness
    day = day_after(9)
    created = await confirm(
        h,
        (
            await intake(
                h,
                "deleted-parent-create",
                "创建",
                {
                    "kind": "create",
                    "title": "删除后接续",
                    "schedule": {
                        "title": "客户面谈",
                        "starts_at": at(day, 10).isoformat(),
                        "ends_at": at(day, 11).isoformat(),
                    },
                    "confidence": 0.9,
                },
            )
        ).json()["draft"],
        "deleted-parent-create-confirm",
    )
    confirmed = created.json()
    matter_id = confirmed["matter"]["matter_id"]
    detail = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}", headers=auth(h.token_a)
        )
    ).json()
    schedule_id = detail["schedules"][0]["schedule_id"]
    parent = await intake(
        h,
        "deleted-parent-update",
        "改到三点",
        {
            "kind": "update",
            "matter_id": matter_id,
            "changes_note": "改到三点",
            "schedule": {
                "title": "客户面谈",
                "starts_at": at(day, 15).isoformat(),
                "ends_at": at(day, 16).isoformat(),
            },
            "confidence": 0.9,
        },
        matter_id=matter_id,
    )
    assert parent.status_code == 201, parent.text
    parent_run_id = parent.json()["local_run_id"]

    async with h.app.state.session_factory() as db:
        schedule = await db.get(ScheduleEvent, schedule_id)
        schedule.deleted_at = datetime.now(UTC)
        schedule.status = "cancelled"
        await db.commit()

    blocked = await h.client.post(
        "/api/v1/matters/intake",
        json={
            "text": "还是改到四点",
            "idempotency_key": "deleted-parent-child",
            "continuation_run_id": parent_run_id,
        },
        headers=auth(h.token_a),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "continuation_parent_changed"


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
    assert reverted.status_code == 409
    assert reverted.json()["error"]["code"] == "creation_revert_unsupported"
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
