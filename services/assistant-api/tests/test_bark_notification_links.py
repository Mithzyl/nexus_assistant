"""Exercise timed and reply notifications and the commute outbox with isolated providers."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from assistant_api.commute.services.bark import BarkNotifier
from assistant_api.core.config import Settings
from assistant_api.models import Matter, MatterTodo, NexusRun, Reminder, ScheduleEvent
from assistant_api.models.bark import BarkNotificationDelivery
from assistant_api.services import bark_notifications
from assistant_api.services.matter_plan import PURPOSE
from conftest import auth
from tests.commute_flow import create_plan, run_full_auto_cycle

WEB_ORIGIN = "https://assistant.example.test"
BARK_API = "/api/v1/commute/bark"


@pytest.mark.parametrize("origin", ["//other.test", "http://other.test", "https://user@other.test", "https://other.test/#x"])
def test_bark_origin_requires_https_origin(origin):
    """Refuse protocol-relative, insecure or credential-bearing deployment addresses."""
    with pytest.raises(ValueError, match="plain HTTPS"):
        Settings(bark_web_origin=origin)


async def test_due_and_reply_links_are_private_and_claimed_once(harness):
    """Send only opted-in account events and do not replay any provider outcome."""
    h = harness
    sent = []

    def receive(request):
        """Capture the actual Bark payload and simulate one ambiguous send."""
        assert request.url.path == "/push"
        payload = json.loads(request.content)
        sent.append(payload)
        if payload["title"] == "日程提醒":
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(200, json={"code": 200})

    h.app.state.bark_transport = httpx.MockTransport(receive)
    h.settings.bark_web_origin = WEB_ORIGIN
    for token in (h.token_a, h.token_b):
        response = await h.client.post(BARK_API + "/binding", headers=auth(token), json={"device_key": "isolated-bark-key"})
        assert not response.json()["assistant_notifications_enabled"]
    response = await h.client.patch(BARK_API, headers=auth(h.token_a), json={"assistant_notifications_enabled": True})
    assert response.json()["assistant_notifications_enabled"]
    assert not response.json()["auto_reminders_enabled"]
    now = datetime.now(UTC) + timedelta(seconds=5)
    async with h.app.state.session_factory() as db:
        matter = Matter(user_id=h.user_a, title="Test matter", status="active")
        other_matter = Matter(user_id=h.user_b, title="Other test matter", status="active")
        db.add_all([matter, other_matter])
        await db.flush()
        reply = NexusRun(user_id=h.user_a, purpose=PURPOSE, status="succeeded", completed_at=now,
                         idempotency_key="test-reply-bark", request_text="private text",
                         reply_state="awaiting_reply", reply_expires_at=now + timedelta(minutes=10))
        db.add_all([
            reply,
            NexusRun(user_id=h.user_a, purpose=PURPOSE, status="succeeded", completed_at=now,
                     idempotency_key="expired-reply-bark", request_text="expired private text",
                     reply_state="awaiting_reply", reply_expires_at=now - timedelta(seconds=1)),
            MatterTodo(user_id=h.user_a, matter_id=matter.matter_id, title="private todo", due_at=now, status="open"),
            MatterTodo(user_id=h.user_b, matter_id=other_matter.matter_id, title="other user's todo", due_at=now, status="open"),
            ScheduleEvent(user_id=h.user_a, title="private schedule", status="scheduled", starts_at=now),
        ])
        await db.commit()
        reply_id = reply.local_run_id
    assert await bark_notifications.tick(h.app, now=now) == 2
    assert await bark_notifications.tick(h.app, now=now + timedelta(seconds=1)) == 0
    assert {item["title"]: item["url"] for item in sent} == {
        "助手等待你的回复": WEB_ORIGIN + "/ask?reply=" + reply_id,
        "待办提醒": WEB_ORIGIN + "/today",
        "日程提醒": WEB_ORIGIN + "/today",
    }
    assert len(sent) == 3
    assert {item["title"]: item["body"] for item in sent} == {
        "助手等待你的回复": "打开生活助手继续回复。",
        "待办提醒": "private todo",
        "日程提醒": "打开生活助手查看今天的安排。",
    }
    async with h.app.state.session_factory() as db:
        rows = list(await db.scalars(select(BarkNotificationDelivery)))
        assert sorted(row.status for row in rows) == ["accepted_by_bark", "accepted_by_bark", "unknown"]
        assert all(row.user_id == h.user_a for row in rows)
        assert await db.scalar(select(ScheduleEvent.schedule_id).where(
            ScheduleEvent.user_id == h.user_a, ScheduleEvent.title == "private schedule",
            ScheduleEvent.status == "scheduled",
        ))
        assert await db.scalar(select(MatterTodo.todo_id).where(
            MatterTodo.user_id == h.user_a, MatterTodo.title == "private todo", MatterTodo.status == "open",
        ))
        assert (await db.get(NexusRun, reply_id)).reply_state == "awaiting_reply"


async def test_bark_uses_current_todo_title_when_claiming_explicit_reminder(harness, monkeypatch):
    """Use a rename made between discovery and claim without changing delivery identity."""
    h = harness
    sent = []

    def receive(request):
        """Capture the outgoing Bark POST."""
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200})

    h.settings.bark_web_origin = WEB_ORIGIN
    h.app.state.bark_transport = httpx.MockTransport(receive)
    await h.client.post(BARK_API + "/binding", headers=auth(h.token_a), json={"device_key": "isolated-bark-key"})
    await h.client.patch(BARK_API, headers=auth(h.token_a), json={"assistant_notifications_enabled": True})
    now = datetime.now(UTC) + timedelta(seconds=5)
    async with h.app.state.session_factory() as db:
        matter = Matter(user_id=h.user_a, title="隔离验收事项", status="active")
        db.add(matter)
        await db.flush()
        todo = MatterTodo(user_id=h.user_a, matter_id=matter.matter_id, title="检查开发进度", status="open")
        db.add(todo)
        await db.flush()
        reminder = Reminder(user_id=h.user_a, matter_id=matter.matter_id, todo_id=todo.todo_id,
                            title="提醒：旧待办标题", trigger_at=now, status="pending")
        db.add(reminder)
        await db.commit()
        todo_id, reminder_id = todo.todo_id, reminder.reminder_id

    scan = bark_notifications._due_notifications

    async def rename_after_scan(db, user_id, since, until):
        """Commit a rename after discovering the pending notification."""
        notifications = await scan(db, user_id, since, until)
        async with h.app.state.session_factory() as other_db:
            todo = await other_db.get(MatterTodo, todo_id)
            todo.title = "检查 Codex 部署进度"
            await other_db.commit()
        return notifications

    monkeypatch.setattr(bark_notifications, "_due_notifications", rename_after_scan)
    assert await bark_notifications.tick(h.app, now=now) == 1
    assert await bark_notifications.tick(h.app, now=now + timedelta(seconds=1)) == 0
    assert len(sent) == 1
    assert sent[0]["title"] == "待办提醒"
    assert sent[0]["body"] == "检查 Codex 部署进度"
    assert sent[0]["url"] == WEB_ORIGIN + "/today"
    async with h.app.state.session_factory() as db:
        delivery = await db.scalar(select(BarkNotificationDelivery))
        assert delivery.status == "accepted_by_bark"
        assert delivery.occurrence == f"todo:{reminder_id}:{now.isoformat()}"


async def test_commute_worker_sends_plan_web_url(commute_harness):
    """Follow the real commute scheduler/outbox and inspect the final Bark POST."""
    h = commute_harness
    sent = []

    def receive(request):
        """Capture the commute notification at its provider boundary."""
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200})

    h.runtime.settings.bark_web_origin = WEB_ORIGIN
    h.runtime.bark = BarkNotifier("isolated-bark-key", transport=httpx.MockTransport(receive))
    plan = await create_plan(h, h.token_a)
    check, outbox = await run_full_auto_cycle(h)
    assert check.status == "succeeded"
    assert outbox.status == "accepted_by_bark"
    assert len(sent) == 1
    assert sent[0]["url"] == f'{WEB_ORIGIN}/me/commute/{plan["id"]}'
