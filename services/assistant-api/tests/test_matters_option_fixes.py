"""Regression tests for option switching, option execution and revert scope."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from assistant_api.models import Matter, MatterChange, MatterTodo, Reminder, ScheduleEvent
from conftest import auth

ZONE = ZoneInfo("Asia/Shanghai")


def at(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


def day_after(days: int = 2):
    return datetime.now(ZONE).date() + timedelta(days=days)


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


async def apply_option(harness, draft: dict, option: str):
    return await harness.client.post(
        f"/api/v1/matters/drafts/{draft['draft_id']}/apply-option",
        json={"expected_version": draft["version"], "option_id": option},
        headers=auth(harness.token_a),
    )


async def setup_matter_with_previous(harness, day):
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


async def create_matter_with_todo(harness, day):
    extraction = {
        "kind": "create",
        "title": "待办保护",
        "goal": "g",
        "schedule": {
            "title": "面谈",
            "starts_at": at(day, 10).isoformat(),
            "ends_at": at(day, 11).isoformat(),
        },
        "todos": [
            {
                "title": "提交材料",
                "due_at": at(day, 9).isoformat(),
                "reminder_at": at(day, 8).isoformat(),
            }
        ],
        "confidence": 0.9,
    }
    draft = (await intake(harness, f"key-todo-{day}", "创建", extraction)).json()["draft"]
    return (await confirm(harness, draft, f"confirm-todo-{day}")).json()


@pytest.mark.asyncio
async def test_switching_or_repeating_options_rebuilds_from_one_baseline(harness):
    h = harness
    day = day_after()
    _, draft = await setup_matter_with_previous(h, day)

    first_b = await apply_option(h, draft, "B")
    assert first_b.status_code == 200, first_b.text
    b_body = first_b.json()
    assert b_body["payload"]["previous_adjustment"]["schedule_id"]
    assert len(b_body["payload"]["affected"]) == 1
    assert len([todo for todo in b_body["payload"]["todos"] if "联系" in todo["title"]]) == 1

    # B -> C must remove B's neighbour shift and contact todo.
    c_after_b = await apply_option(h, b_body, "C")
    assert c_after_b.status_code == 200, c_after_b.text
    c_body = c_after_b.json()
    assert "previous_adjustment" not in c_body["payload"]
    assert c_body["payload"]["affected"] == []
    assert not [todo for todo in c_body["payload"]["todos"] if "联系" in todo["title"]]
    assert c_body["payload"]["schedule"]["starts_at"].startswith(
        at(day, 14).strftime("%Y-%m-%dT%H:%M")
    )

    # B twice must not duplicate the contact todo; A after B must not retain B.
    second_b = await apply_option(h, c_body, "B")
    assert second_b.status_code == 200
    third_b = await apply_option(h, second_b.json(), "B")
    assert third_b.status_code == 200
    assert len(
        [todo for todo in third_b.json()["payload"]["todos"] if "联系" in todo["title"]]
    ) == 1

    a_after_b = await apply_option(h, third_b.json(), "A")
    assert a_after_b.status_code == 200, a_after_b.text
    a_body = a_after_b.json()
    assert "previous_adjustment" not in a_body["payload"]
    assert a_body["payload"]["affected"] == []
    assert not [todo for todo in a_body["payload"]["todos"] if "联系" in todo["title"]]
    assert a_body["payload"]["schedule"]["starts_at"].startswith(
        at(day, 14, 10).strftime("%Y-%m-%dT%H:%M")
    )

    done = await confirm(h, a_body, f"confirm-switch-{day}")
    assert done.status_code == 200, done.text
    today = (
        await h.client.get(
            f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a)
        )
    ).json()
    previous = [item for item in today["schedules"] if item["title"] == "牙医"][0]
    assert previous["ends_at"].startswith(at(day, 13, 55).strftime("%Y-%m-%dT%H:%M"))


@pytest.mark.asyncio
async def test_overlap_option_moves_past_long_event_and_rechecks(harness):
    h = harness
    day = day_after(5)
    extraction = {
        "kind": "create",
        "title": "长会冲突",
        "schedule": {
            "title": "面谈",
            "starts_at": at(day, 15).isoformat(),
            "ends_at": at(day, 16).isoformat(),
        },
        "confidence": 0.9,
    }
    created = (await intake(h, f"key-long-{day}", "创建", extraction)).json()["draft"]
    matter = (await confirm(h, created, f"confirm-long-{day}")).json()["matter"]
    async with h.app.state.session_factory() as db:
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
        await intake(h, f"key-long-update-{day}", "改到两点", update, matter_id=matter["matter_id"])
    ).json()["draft"]
    assert draft["conflicts"]["severity"] == "overlap"
    applied = await apply_option(h, draft, "A")
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["payload"]["schedule"]["starts_at"].startswith(
        at(day, 18, 15).strftime("%Y-%m-%dT%H:%M")
    )
    assert body["conflicts"]["has_conflict"] is False, body["conflicts"]
    done = await confirm(h, body, f"confirm-long-update-{day}")
    assert done.status_code == 200, done.text
    detail = (
        await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    ).json()
    assert detail["schedules"][0]["starts_at"].startswith(at(day, 18, 15).strftime("%Y-%m-%dT%H:%M"))


@pytest.mark.asyncio
async def test_option_b_rejects_read_only_apple_copy(harness):
    h = harness
    day = day_after(6)
    _, draft = await setup_matter_with_previous(h, day)
    async with h.app.state.session_factory() as db:
        previous = await db.scalar(
            select(ScheduleEvent).where(ScheduleEvent.title == "牙医")
        )
        previous.source = "ios_eventkit"
        previous.sync_status = "ios_synced"
        await db.commit()
        previous_id = previous.schedule_id
        previous_starts = previous.starts_at

    fresh = await h.client.get(
        f"/api/v1/matters/drafts/{draft['draft_id']}", headers=auth(h.token_a)
    )
    assert fresh.status_code == 200, fresh.text
    options = {item["option_id"]: item for item in fresh.json()["conflicts"]["options"]}
    assert options["B"]["executable"] is False
    assert "只读" in options["B"]["unavailable_reason"]

    rejected = await apply_option(h, fresh.json(), "B")
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "option_not_executable"
    async with h.app.state.session_factory() as db:
        unchanged = await db.get(ScheduleEvent, previous_id)
        assert unchanged.starts_at == previous_starts


@pytest.mark.asyncio
async def test_revert_option_b_restores_neighbour_and_removes_contact_todo(harness):
    h = harness
    day = day_after(7)
    matter, draft = await setup_matter_with_previous(h, day)
    other = await h.client.post(
        "/api/v1/matters",
        json={"title": "另一个事项"},
        headers=auth(h.token_a),
    )
    assert other.status_code == 201, other.text
    other_matter_id = other.json()["matter_id"]
    async with h.app.state.session_factory() as db:
        previous = await db.scalar(select(ScheduleEvent).where(ScheduleEvent.title == "牙医"))
        previous.matter_id = other_matter_id
        await db.commit()

    applied = await apply_option(h, draft, "B")
    assert applied.status_code == 200, applied.text
    body = applied.json()
    affected = body["payload"]["affected"][0]
    assert affected["matter_id"] == other_matter_id
    assert affected["matter_title"] == "另一个事项"
    assert affected["before_starts_at"] and affected["after_starts_at"]
    reloaded = await h.client.get(
        f"/api/v1/matters/drafts/{body['draft_id']}", headers=auth(h.token_a)
    )
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["conflicts"]["has_conflict"] is False
    done = await confirm(h, body, f"confirm-b-{day}")
    assert done.status_code == 200, done.text
    confirmed = done.json()

    today = (
        await h.client.get(f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a))
    ).json()
    assert [item for item in today["schedules"] if item["title"] == "牙医"][0]["ends_at"].startswith(
        at(day, 13, 45).strftime("%Y-%m-%dT%H:%M")
    )
    detail = (
        await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    ).json()
    assert any("联系" in todo["title"] for todo in detail["todos"])

    affected_changes = (
        await h.client.get(
            f"/api/v1/matters/{other_matter_id}/changes",
            headers=auth(h.token_a),
        )
    ).json()
    affected_change = next(
        item for item in affected_changes if item["change_id"] == confirmed["change_id"]
    )
    assert affected_change["relation"] == "affected"
    assert affected_change["source_matter_id"] == matter["matter_id"]
    assert affected_change["source_matter_title"] == matter["title"]
    assert affected_change["revertable"] is False
    assert affected_change["reason_code"] == "revert_from_source_matter"
    other_detail = (
        await h.client.get(
            f"/api/v1/matters/{other_matter_id}",
            headers=auth(h.token_a),
        )
    ).json()
    wrong_entry = await h.client.post(
        f"/api/v1/matters/{other_matter_id}/changes/{confirmed['change_id']}/revert",
        json={"expected_version": other_detail["version"]},
        headers=auth(h.token_a),
    )
    assert wrong_entry.status_code == 409, wrong_entry.text
    assert wrong_entry.json()["error"]["code"] == "revert_from_source_matter"

    source_changes = (
        await h.client.get(
            f"/api/v1/matters/{matter['matter_id']}/changes",
            headers=auth(h.token_a),
        )
    ).json()
    source_change = next(
        item for item in source_changes if item["change_id"] == confirmed["change_id"]
    )
    assert source_change["relation"] == "primary"
    assert source_change["affected_matter_ids"] == [other_matter_id]

    reverted = await h.client.post(
        f"/api/v1/matters/{matter['matter_id']}/changes/{confirmed['change_id']}/revert",
        json={"expected_version": confirmed["matter"]["version"]},
        headers=auth(h.token_a),
    )
    assert reverted.status_code == 200, reverted.text
    after = (
        await h.client.get(f"/api/v1/matters/{matter['matter_id']}", headers=auth(h.token_a))
    ).json()
    today_after = (
        await h.client.get(f"/api/v1/today?date={day.isoformat()}", headers=auth(h.token_a))
    ).json()
    previous = [item for item in today_after["schedules"] if item["title"] == "牙医"][0]
    assert previous["starts_at"].startswith(at(day, 13).strftime("%Y-%m-%dT%H:%M"))
    assert previous["ends_at"].startswith(at(day, 13, 55).strftime("%Y-%m-%dT%H:%M"))
    assert not any("联系" in todo["title"] for todo in after["todos"])
    current = [item for item in after["schedules"] if item["title"] == "客户面谈"][0]
    assert current["starts_at"].startswith(at(day, 15).strftime("%Y-%m-%dT%H:%M"))


@pytest.mark.asyncio
async def test_confirm_key_and_direct_edit_key_reject_a_different_target(harness):
    h = harness
    day = day_after(8)
    first = (
        await intake(h, f"key-one-{day}", "第一件", {
            "kind": "create", "title": "第一件", "confidence": 0.9,
        })
    ).json()["draft"]
    second = (
        await intake(h, f"key-two-{day}", "第二件", {
            "kind": "create", "title": "第二件", "confidence": 0.9,
        })
    ).json()["draft"]
    assert (await confirm(h, first, f"same-confirm-{day}")).status_code == 200
    reused = await confirm(h, second, f"same-confirm-{day}")
    assert reused.status_code == 409, reused.text
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    created = await create_matter_with_todo(h, day)
    detail = (
        await h.client.get(
            f"/api/v1/matters/{created['matter']['matter_id']}", headers=auth(h.token_a)
        )
    ).json()
    schedule_id = detail["schedules"][0]["schedule_id"]
    todo_id = detail["todos"][0]["todo_id"]
    preview = await h.client.post(
        f"/api/v1/matters/{created['matter']['matter_id']}/drafts",
        json={"idempotency_key": "direct-same-key", "schedule_id": schedule_id},
        headers=auth(h.token_a),
    )
    assert preview.status_code == 201, preview.text
    other_target = await h.client.post(
        f"/api/v1/matters/{created['matter']['matter_id']}/drafts",
        json={"idempotency_key": "direct-same-key", "todo_id": todo_id},
        headers=auth(h.token_a),
    )
    assert other_target.status_code == 409, other_target.text
    assert other_target.json()["error"]["code"] == "idempotency_key_reused"


@pytest.mark.asyncio
async def test_todo_changed_after_preview_is_not_overwritten(harness):
    h = harness
    day = day_after(9)
    created = await create_matter_with_todo(h, day)
    matter_id = created["matter"]["matter_id"]
    detail = (
        await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))
    ).json()
    todo = detail["todos"][0]
    preview = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"idempotency_key": "direct-todo-protect", "todo_id": todo["todo_id"]},
        headers=auth(h.token_a),
    )
    assert preview.status_code == 201, preview.text
    draft = preview.json()
    completed = await h.client.post(
        f"/api/v1/matters/{matter_id}/todos/{todo['todo_id']}/complete",
        headers=auth(h.token_a),
    )
    assert completed.status_code == 200
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "todos": [
                {
                    "todo_id": todo["todo_id"],
                    "title": todo["title"],
                    "due_at": at(day, 18).isoformat(),
                    "reminder_at": at(day, 17).isoformat(),
                }
            ],
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    rejected = await confirm(h, patched.json(), f"confirm-todo-protect-{day}")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "todo_changed"
    after = (
        await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))
    ).json()["todos"][0]
    assert after["status"] == "done"
    assert after["due_at"].startswith(at(day, 9).strftime("%Y-%m-%dT%H:%M"))


@pytest.mark.asyncio
async def test_create_change_is_not_claimed_revertable(harness):
    h = harness
    day = day_after(10)
    draft = (
        await intake(h, f"key-create-revert-{day}", "创建", {
            "kind": "create", "title": "不能假装撤回", "confidence": 0.9,
        })
    ).json()["draft"]
    confirmed = (await confirm(h, draft, f"confirm-create-revert-{day}")).json()
    matter_id = confirmed["matter"]["matter_id"]
    listing = (
        await h.client.get(f"/api/v1/matters/{matter_id}/changes", headers=auth(h.token_a))
    ).json()
    assert listing[0]["revertable"] is False
    assert listing[0]["reason_code"] == "creation_revert_unsupported"
    response = await h.client.post(
        f"/api/v1/matters/{matter_id}/changes/{confirmed['change_id']}/revert",
        json={"expected_version": confirmed["matter"]["version"]},
        headers=auth(h.token_a),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "creation_revert_unsupported"


@pytest.mark.asyncio
async def test_manual_patch_discards_previous_option(harness):
    h = harness
    day = day_after(11)
    _, draft = await setup_matter_with_previous(h, day)
    applied = await apply_option(h, draft, "A")
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["payload"]["schedule"]["starts_at"].startswith(
        at(day, 14, 10).strftime("%Y-%m-%dT%H:%M")
    )

    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={"expected_version": body["version"], "title": "客户面谈（改）"},
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    after = patched.json()
    assert after["payload"]["schedule"]["starts_at"].startswith(
        at(day, 14).strftime("%Y-%m-%dT%H:%M")
    )
    assert "selected_option" not in after["payload"]
    assert "previous_adjustment" not in after["payload"]
    assert not after["payload"].get("affected")


@pytest.mark.asyncio
async def test_todo_and_reminder_change_is_recorded_and_reverted(harness):
    h = harness
    day = day_after(12)
    created = await create_matter_with_todo(h, day)
    matter_id = created["matter"]["matter_id"]
    detail = (
        await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))
    ).json()
    todo = detail["todos"][0]
    preview = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"idempotency_key": "direct-todo-revert", "todo_id": todo["todo_id"]},
        headers=auth(h.token_a),
    )
    assert preview.status_code == 201, preview.text
    draft = preview.json()
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "todos": [
                {
                    "todo_id": todo["todo_id"],
                    "title": todo["title"],
                    "due_at": at(day, 18).isoformat(),
                    "reminder_at": at(day, 17).isoformat(),
                }
            ],
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    done = await confirm(h, patched.json(), f"confirm-todo-revert-{day}")
    assert done.status_code == 200, done.text
    confirmed = done.json()
    changes = (
        await h.client.get(f"/api/v1/matters/{matter_id}/changes", headers=auth(h.token_a))
    ).json()
    change = next(item for item in changes if item["change_id"] == confirmed["change_id"])
    assert change["before"]["todos"] and change["after"]["todos"]
    assert change["before"]["reminders"] and change["after"]["reminders"]
    assert change["revertable"] is True

    reverted = await h.client.post(
        f"/api/v1/matters/{matter_id}/changes/{confirmed['change_id']}/revert",
        json={"expected_version": confirmed["matter"]["version"]},
        headers=auth(h.token_a),
    )
    assert reverted.status_code == 200, reverted.text
    after = (
        await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))
    ).json()
    restored = after["todos"][0]
    assert restored["due_at"].startswith(at(day, 9).strftime("%Y-%m-%dT%H:%M"))
    assert restored["reminder_at"].startswith(at(day, 8).strftime("%Y-%m-%dT%H:%M"))
    async with h.app.state.session_factory() as db:
        reminder = await db.scalar(
            select(Reminder).where(Reminder.todo_id == todo["todo_id"])
        )
        assert reminder.status == "pending"
        assert reminder.trigger_at is not None


@pytest.mark.asyncio
async def test_legacy_change_with_incomplete_snapshot_is_rejected(harness):
    h = harness
    created = await h.client.post(
        "/api/v1/matters",
        json={"title": "旧快照事项"},
        headers=auth(h.token_a),
    )
    assert created.status_code == 201, created.text
    matter_id = created.json()["matter_id"]

    day = day_after(13)
    async with h.app.state.session_factory() as db:
        matter = await db.get(Matter, matter_id)
        assert matter is not None
        schedule = ScheduleEvent(
            user_id=h.user_a,
            matter_id=matter_id,
            title="旧日程",
            starts_at=at(day, 10),
            ends_at=at(day, 11),
            status="scheduled",
            source="web_native",
            sync_status="local_only",
        )
        db.add(schedule)
        await db.flush()
        legacy_change = MatterChange(
            matter_id=matter_id,
            user_id=h.user_a,
            change_type="update",
            before_json={
                "matter": {
                    "title": matter.title,
                    "goal": matter.goal,
                    "status": matter.status,
                    "next_step": matter.next_step,
                }
            },
            after_json={
                "matter": {
                    "title": matter.title,
                    "goal": matter.goal,
                    "status": matter.status,
                    "next_step": matter.next_step,
                },
                "matter_version": matter.version,
                "schedule": {
                    "schedule_id": schedule.schedule_id,
                    "title": schedule.title,
                    "starts_at": schedule.starts_at.isoformat(),
                    "ends_at": schedule.ends_at.isoformat(),
                    "location": None,
                },
            },
        )
        db.add(legacy_change)
        await db.commit()
        change_id = legacy_change.change_id

    listing = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}/changes",
            headers=auth(h.token_a),
        )
    ).json()
    item = next(row for row in listing if row["change_id"] == change_id)
    assert item["revertable"] is False
    assert item["reason_code"] == "incomplete_change_snapshot"

    rejected = await h.client.post(
        f"/api/v1/matters/{matter_id}/changes/{change_id}/revert",
        json={"expected_version": created.json()["version"]},
        headers=auth(h.token_a),
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "incomplete_change_snapshot"


@pytest.mark.asyncio
async def test_stale_option_b_cannot_overwrite_changed_neighbour(harness):
    h = harness
    day = day_after(14)
    matter, draft = await setup_matter_with_previous(h, day)
    other = await h.client.post(
        "/api/v1/matters",
        json={"title": "相邻日程事项"},
        headers=auth(h.token_a),
    )
    assert other.status_code == 201, other.text
    other_matter_id = other.json()["matter_id"]
    async with h.app.state.session_factory() as db:
        neighbour = await db.scalar(
            select(ScheduleEvent).where(ScheduleEvent.title == "牙医")
        )
        assert neighbour is not None
        neighbour.matter_id = other_matter_id
        await db.commit()
        neighbour_id = neighbour.schedule_id

    applied = await apply_option(h, draft, "B")
    assert applied.status_code == 200, applied.text
    body = applied.json()

    direct = await h.client.post(
        f"/api/v1/matters/{other_matter_id}/drafts",
        json={
            "idempotency_key": f"direct-neighbour-{day}",
            "schedule_id": neighbour_id,
        },
        headers=auth(h.token_a),
    )
    assert direct.status_code == 201, direct.text
    direct_draft = direct.json()
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{direct_draft['draft_id']}",
        json={
            "expected_version": direct_draft["version"],
            "schedule": {
                "schedule_id": neighbour_id,
                "title": "牙医",
                "starts_at": at(day, 13, 10).isoformat(),
                "ends_at": at(day, 13, 50).isoformat(),
            },
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    direct_done = await confirm(
        h, patched.json(), f"confirm-direct-neighbour-{day}"
    )
    assert direct_done.status_code == 200, direct_done.text

    rejected = await confirm(h, body, f"confirm-stale-b-{day}")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "schedule_changed"

    async with h.app.state.session_factory() as db:
        neighbour = await db.get(ScheduleEvent, neighbour_id)
        assert neighbour is not None
        assert neighbour.starts_at.replace(tzinfo=UTC) == at(day, 13, 10)
        contact = await db.scalar(
            select(MatterTodo).where(MatterTodo.title.like("联系%"))
        )
        assert contact is None
    detail = (
        await h.client.get(
            f"/api/v1/matters/{matter['matter_id']}",
            headers=auth(h.token_a),
        )
    ).json()
    current = [item for item in detail["schedules"] if item["title"] == "客户面谈"][0]
    assert current["starts_at"].startswith(at(day, 15).strftime("%Y-%m-%dT%H:%M"))


@pytest.mark.asyncio
async def test_reminder_changed_after_confirmation_blocks_revert(harness):
    h = harness
    day = day_after(15)
    created = await create_matter_with_todo(h, day)
    matter_id = created["matter"]["matter_id"]
    detail = (
        await h.client.get(f"/api/v1/matters/{matter_id}", headers=auth(h.token_a))
    ).json()
    todo = detail["todos"][0]
    preview = await h.client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={
            "idempotency_key": "direct-todo-reminder-protect",
            "todo_id": todo["todo_id"],
        },
        headers=auth(h.token_a),
    )
    assert preview.status_code == 201, preview.text
    draft = preview.json()
    patched = await h.client.patch(
        f"/api/v1/matters/drafts/{draft['draft_id']}",
        json={
            "expected_version": draft["version"],
            "todos": [
                {
                    "todo_id": todo["todo_id"],
                    "title": todo["title"],
                    "due_at": at(day, 18).isoformat(),
                    "reminder_at": at(day, 17).isoformat(),
                }
            ],
        },
        headers=auth(h.token_a),
    )
    assert patched.status_code == 200, patched.text
    done = await confirm(h, patched.json(), f"confirm-reminder-protect-{day}")
    assert done.status_code == 200, done.text
    confirmed = done.json()

    async with h.app.state.session_factory() as db:
        reminder = await db.scalar(
            select(Reminder).where(Reminder.todo_id == todo["todo_id"])
        )
        assert reminder is not None
        reminder.trigger_at = reminder.trigger_at + timedelta(minutes=5)
        await db.commit()

    changes = (
        await h.client.get(
            f"/api/v1/matters/{matter_id}/changes",
            headers=auth(h.token_a),
        )
    ).json()
    change = next(item for item in changes if item["change_id"] == confirmed["change_id"])
    assert change["revertable"] is False
    assert change["reason_code"] == "reminder_changed"
    rejected = await h.client.post(
        f"/api/v1/matters/{matter_id}/changes/{confirmed['change_id']}/revert",
        json={"expected_version": confirmed["matter"]["version"]},
        headers=auth(h.token_a),
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "reminder_changed"
