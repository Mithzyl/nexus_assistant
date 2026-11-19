"""Deliver due business events through Web Push without a page timer or model call."""
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from assistant_api.core.observability import emit_event
from assistant_api.models import User, Matter, MatterTodo, ScheduleEvent, Reminder, CommutePlan
from assistant_api.models.base import as_utc
from assistant_api.models.browser_push import BrowserPushSubscription, BrowserPushDelivery

logger = logging.getLogger(__name__)


async def due_events(db, user_id, since, now):
    """Find due active reminders and default event times, suppressing explicit-alarm duplicates."""
    matters = {row.matter_id: row for row in await db.scalars(select(Matter).where(Matter.user_id == user_id))}
    todos = {row.todo_id: row for row in await db.scalars(select(MatterTodo).where(MatterTodo.user_id == user_id))}
    schedules = {row.schedule_id: row for row in await db.scalars(select(ScheduleEvent).where(ScheduleEvent.user_id == user_id))}
    plans = {row.matter_id: row for row in await db.scalars(select(CommutePlan).where(CommutePlan.user_id == user_id)) if row.matter_id}
    reminders = list(await db.scalars(select(Reminder).where(Reminder.user_id == user_id, Reminder.deleted_at.is_(None))))

    def active(matter_id):
        """Exclude stopped or deleted matters while retaining standalone records."""
        row = matters.get(matter_id)
        return matter_id is None or bool(row and row.deleted_at is None and row.status in {'active', 'waiting'})

    events = []
    explicit_todos, explicit_schedules = set(), set()
    for row in reminders:
        if row.status in {'cancelled', 'completed'} or row.acknowledged_at or not active(row.matter_id):
            continue
        todo, schedule = todos.get(row.todo_id), schedules.get(row.schedule_id)
        if row.todo_id and (not todo or todo.deleted_at or todo.status != 'open'):
            continue
        if row.schedule_id and (not schedule or schedule.deleted_at or schedule.status != 'scheduled'):
            continue
        if todo: explicit_todos.add(todo.todo_id)
        if schedule: explicit_schedules.add(schedule.schedule_id)
        plan = plans.get(row.matter_id) if schedule else None
        if plan and (not plan.enabled or not plan.notify_enabled):
            continue
        kind = 'commute' if plan else 'todo' if todo else 'schedule' if schedule else 'reminder'
        events.append((kind, row.reminder_id, row.trigger_at, row.title, row.matter_id))
    for row in todos.values():
        at = row.reminder_at or row.due_at
        if at and row.todo_id not in explicit_todos and not row.deleted_at and row.status == 'open' and active(row.matter_id):
            events.append(('todo', row.todo_id, at, row.title, row.matter_id))
    for row in schedules.values():
        if row.deleted_at or row.status != 'scheduled' or row.schedule_id in explicit_schedules or not active(row.matter_id):
            continue
        plan = plans.get(row.matter_id)
        if plan and (not plan.enabled or not plan.notify_enabled):
            continue
        kind = 'commute' if plan else 'schedule'
        events.append((kind, row.schedule_id, row.starts_at, row.title, row.matter_id))
    return [event for event in events if since <= as_utc(event[2]) <= now]


async def send_web_push(settings, subscription, payload):
    """Encrypt for the browser endpoint; never log capability URLs, keys or response bodies."""
    from pywebpush import webpush
    await asyncio.to_thread(webpush,
        subscription_info=subscription, data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=str(settings.web_push_private_key_path),
        vapid_claims={'sub': settings.web_push_contact}, ttl=3600, timeout=15,
        headers={'Urgency': 'high'})


async def tick(app, *, now=None):
    """Queue and atomically claim due occurrences; ambiguous sends are not replayed."""
    settings = app.state.settings
    if not settings.web_push_enabled:
        return 0
    now = now or datetime.now(UTC)
    async with app.state.session_factory() as db:
        subscriptions = list(await db.scalars(select(BrowserPushSubscription).join(User).where(
            BrowserPushSubscription.enabled.is_(True), User.status == 'active').limit(500)))
        for subscription in subscriptions:
            since = max(as_utc(subscription.enabled_at), now - timedelta(hours=24))
            for kind, resource_id, at, title, matter_id in await due_events(db, subscription.user_id, since, now):
                at = as_utc(at)
                occurrence = f'{kind}:{resource_id}:{at.isoformat()}'
                existing = await db.scalar(select(BrowserPushDelivery.delivery_id).where(
                    BrowserPushDelivery.subscription_id == subscription.subscription_id,
                    BrowserPushDelivery.occurrence == occurrence))
                if existing: continue
                payload = {'title': {'todo': '待办提醒', 'schedule': '日程提醒', 'commute': '通勤出发提醒', 'reminder': '到点提醒'}[kind],
                           'body': title, 'kind': kind, 'tag': occurrence,
                           'url': '/matters/' + matter_id if matter_id else '/today'}
                db.add(BrowserPushDelivery(subscription_id=subscription.subscription_id, user_id=subscription.user_id,
                    occurrence=occurrence, resource_kind=kind, resource_id=resource_id, trigger_at=at, payload_json=payload))
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()  # Another worker already queued this same occurrence.
        pending = list(await db.scalars(select(BrowserPushDelivery).where(BrowserPushDelivery.status == 'pending').limit(50)))
        count = 0
        for row in pending:
            subscription = await db.get(BrowserPushSubscription, row.subscription_id)
            if not subscription or not subscription.enabled or subscription.user_id != row.user_id:
                row.status = 'cancelled'; await db.commit(); continue
            # Re-read business state immediately before transmission, including edits/cancellation.
            current = await due_events(db, row.user_id, as_utc(subscription.enabled_at), now)
            if not any(f'{kind}:{identity}:{as_utc(at).isoformat()}' == row.occurrence for kind, identity, at, *_ in current):
                row.status = 'cancelled'; await db.commit(); continue
            claim = await db.execute(update(BrowserPushDelivery).where(
                BrowserPushDelivery.delivery_id == row.delivery_id, BrowserPushDelivery.status == 'pending').values(status='sending'))
            await db.commit()
            if claim.rowcount != 1: continue
            payload = {**row.payload_json, 'delivery_id': row.delivery_id}
            try:
                sender = getattr(app.state, 'web_push_sender', None)
                if sender:
                    await sender({'endpoint': subscription.endpoint, 'keys': subscription.keys_json}, payload)
                else:
                    await send_web_push(settings, {'endpoint': subscription.endpoint, 'keys': subscription.keys_json}, payload)
            except Exception as error:
                response = getattr(error, 'response', None)
                status = getattr(response, 'status_code', None)
                if status in {404, 410}:
                    subscription.enabled = False
                    row.status = 'expired'
                else:
                    row.status = 'unknown'
                row.error_code = 'push_endpoint_expired' if status in {404, 410} else 'push_outcome_unknown'
                logger.warning('browser push not accepted delivery=%s status=%s', row.delivery_id, row.status)
            else:
                # The service worker may have acknowledged display while the HTTP request was finishing.
                await db.refresh(row)
                if row.status != 'displayed': row.status = 'accepted'
                row.accepted_at = now
                count += 1
            await db.commit()
        return count


async def run_worker(app, stop):
    """Keep browser notifications moving independently of long AI/follow-up tasks."""
    while not stop.is_set():
        try:
            await tick(app)
        except Exception:
            emit_event('background_tick_failure',level='error',category='queue_failure',component='browser_push')
        try:
            await asyncio.wait_for(stop.wait(), timeout=3)
        except TimeoutError:
            pass
