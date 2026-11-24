"""Deliver opted-in Assistant notifications independently of browser subscriptions."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.commute.services.bark import BarkNotifier
from assistant_api.core.observability import emit_event
from assistant_api.models import NexusRun, User
from assistant_api.models.bark import BarkNotificationDelivery, BarkReminderSetting
from assistant_api.models.base import as_utc
from assistant_api.services.browser_push import due_events
from assistant_api.services.matter_plan import PURPOSE

REMINDER_WINDOW = timedelta(minutes=30)
SEND_TIMEOUT_WINDOW = timedelta(minutes=2)
TIMED_TITLES = {"todo": "待办提醒", "schedule": "日程提醒", "reminder": "到点提醒"}


@dataclass(frozen=True)
class BarkNotification:
    """Keep only the information needed to claim and send one notification."""

    occurrence: str
    title: str
    body: str
    trigger_at: datetime
    reply_run_id: str | None = None


def _timed_occurrence(kind: str, resource_id: str, trigger_at: datetime) -> str:
    """Identify a timed event by its record and current reminder time."""
    return f"{kind}:{resource_id}:{as_utc(trigger_at).isoformat()}"


async def _due_notifications(
    db: AsyncSession, user_id: str, since: datetime, now: datetime,
) -> list[BarkNotification]:
    """Read current business reminders and saved clarifications for one account."""
    notifications = [
        BarkNotification(
            _timed_occurrence(kind, resource_id, trigger_at),
            TIMED_TITLES[kind], "打开生活助手查看今天的安排。", as_utc(trigger_at),
        )
        for kind, resource_id, trigger_at, _, _ in await due_events(db, user_id, since, now)
        if kind in TIMED_TITLES
    ]
    # Commute alerts already run through the commute outbox with their own opt-in.
    replies = await db.execute(select(NexusRun.local_run_id, NexusRun.completed_at).where(
        NexusRun.user_id == user_id, NexusRun.purpose == PURPOSE, NexusRun.status == "succeeded",
        NexusRun.reply_state == "awaiting_reply", NexusRun.reply_expires_at > now,
        NexusRun.completed_at.between(since, now),
    ))
    notifications.extend(
        BarkNotification(
            f"reply:{run_id}", "助手等待你的回复", "打开生活助手继续回复。", as_utc(completed_at), run_id,
        )
        for run_id, completed_at in replies
    )
    return notifications


async def _claim(
    db: AsyncSession, user_id: str, notification: BarkNotification, now: datetime,
) -> tuple[str, str] | None:
    """Re-check the current binding and claim the occurrence before contacting Bark."""
    binding = (await db.execute(select(
        BarkReminderSetting.device_key, BarkReminderSetting.assistant_notifications_enabled_at,
    ).join(User).where(
        BarkReminderSetting.user_id == user_id, User.status == "active",
    ))).one_or_none()
    if binding is None:
        return None
    key, enabled_at = binding
    if not key or not enabled_at:
        return None
    since = max(as_utc(enabled_at), now - REMINDER_WINDOW)
    if notification.trigger_at < since:
        return None
    existing = select(BarkNotificationDelivery.delivery_id).where(
        BarkNotificationDelivery.user_id == user_id,
        BarkNotificationDelivery.occurrence == notification.occurrence,
    )
    if await db.scalar(existing):
        return None
    if notification.reply_run_id:
        still_due = await db.scalar(select(NexusRun.local_run_id).where(
            NexusRun.local_run_id == notification.reply_run_id, NexusRun.user_id == user_id,
            NexusRun.status == "succeeded", NexusRun.reply_state == "awaiting_reply",
            NexusRun.reply_expires_at > now,
        ))
    else:
        still_due = any(
            _timed_occurrence(kind, resource_id, at) == notification.occurrence
            for kind, resource_id, at, _, _ in await due_events(db, user_id, since, now)
        )
    if not still_due:
        return None
    delivery = BarkNotificationDelivery(user_id=user_id, occurrence=notification.occurrence, status="sending")
    db.add(delivery)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if await db.scalar(existing):
            return None
        raise
    return delivery.delivery_id, key


async def tick(app, *, now: datetime | None = None) -> int:
    """Find, claim and send due notifications, saving only the provider result."""
    settings = app.state.settings
    origin = settings.bark_web_origin
    if not origin:
        return 0
    poll_time = now or datetime.now(UTC)
    async with app.state.session_factory() as db:
        await db.execute(update(BarkNotificationDelivery).where(
            BarkNotificationDelivery.status == "sending",
            BarkNotificationDelivery.updated_at < poll_time - SEND_TIMEOUT_WINDOW,
        ).values(status="unknown", error_code="bark_send_interrupted"))
        recipients = (await db.execute(select(
            BarkReminderSetting.user_id, BarkReminderSetting.assistant_notifications_enabled_at,
        ).join(User).where(
            User.status == "active", BarkReminderSetting.device_key.is_not(None),
            BarkReminderSetting.assistant_notifications_enabled_at.is_not(None),
        ))).all()
        await db.commit()

    count = 0
    for user_id, enabled_at in recipients:
        since = max(as_utc(enabled_at), poll_time - REMINDER_WINDOW)
        async with app.state.session_factory() as db:
            notifications = await _due_notifications(db, user_id, since, poll_time)
        for notification in notifications:
            async with app.state.session_factory() as db:
                claim = await _claim(db, user_id, notification, now or datetime.now(UTC))
            if claim is None:
                continue
            delivery_id, key = claim
            reply_id = notification.reply_run_id
            path = f"/ask?reply={quote(reply_id, safe='')}" if reply_id else "/today"
            notifier = BarkNotifier(
                key, timeout_seconds=settings.bark_timeout_seconds, transport=app.state.bark_transport,
            )
            try:
                result = await notifier.send(
                    title=notification.title, body=notification.body, group="生活助手", url=origin + path,
                )
            finally:
                await notifier.aclose()
            accepted = result.status == "accepted"
            async with app.state.session_factory() as db:
                saved = await db.execute(update(BarkNotificationDelivery).where(
                    BarkNotificationDelivery.delivery_id == delivery_id,
                    BarkNotificationDelivery.status == "sending",
                ).values(
                    status="accepted_by_bark" if accepted else result.status,
                    error_code=result.error_code,
                    accepted_at=(now or datetime.now(UTC)) if accepted else None,
                ))
                await db.commit()
            if accepted and saved.rowcount == 1:
                count += 1
    return count


async def run_worker(app, stop: asyncio.Event) -> None:
    """Poll notification events without waiting on model or provider jobs in other queues."""
    while not stop.is_set():
        try:
            await tick(app)
        except Exception:
            emit_event("background_tick_failure", level="error", category="queue_failure", component="bark_notifications")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
