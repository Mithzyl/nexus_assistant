"""Reminder persistence with explicit owner and optional schedule-time dependency.

Sharing a matter or transaction never implies a schedule association. Only a
schedule's own reminder or an explicit relative request writes schedule_id.
These functions flush as needed but leave commit/rollback to the business caller.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import or_, select
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import MatterTodo, Reminder, ScheduleEvent
from assistant_api.core.errors import InvalidRequestError, NotFoundError


def absolute_time(value, timezone: str):
    """Interpret an optional user timestamp using the same timezone rule as record input."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    return (parsed.replace(tzinfo=ZoneInfo(timezone)) if parsed.tzinfo is None else parsed).astimezone(UTC)


async def todo_timing(db, user, *, matter_id, item, timezone, current_schedule=None):
    """Resolve absolute timing or an explicitly selected schedule dependency, without writes."""
    relation = item.get('schedule_reminder')
    if relation is None:
        return absolute_time(item.get('reminder_at'), timezone), None, None
    if item.get('reminder_at') is not None:
        raise InvalidRequestError('reminder_timing_conflict', '请选择一种提醒时间。')
    schedule_id = relation.get('schedule_id') or (current_schedule.schedule_id if current_schedule else None)
    if not schedule_id:
        raise InvalidRequestError('reminder_schedule_required', '请指定提醒所依据的日程。')
    schedule = await db.scalar(select(ScheduleEvent).where(
        ScheduleEvent.schedule_id == schedule_id, ScheduleEvent.user_id == user.user_id,
        ScheduleEvent.deleted_at.is_(None), ScheduleEvent.status == 'scheduled'))
    if schedule is None:
        raise NotFoundError('schedule_not_found', '提醒所依据的日程不存在。')
    if schedule.matter_id != matter_id:
        raise InvalidRequestError('reminder_schedule_not_in_matter', '请将相对提醒待办添加到该日程所属事项。')
    offset = relation['offset_seconds']
    return ensure_aware(schedule.starts_at) + timedelta(seconds=offset), schedule.schedule_id, offset


async def sync_todo_reminder(db, user, *, matter_id, todo, timing, pending, sync_to_ios=False):
    """Save a todo-owned reminder; an unrelated calendar record is never attached implicitly."""
    trigger_at, schedule_id, offset = timing
    if trigger_at is None or todo.status == 'done':
        if pending is not None:
            pending.status = 'cancelled'
            pending.cancelled_at = datetime.now(UTC)
        return pending, False
    if pending is None:
        pending = Reminder(user_id=user.user_id, matter_id=matter_id, todo_id=todo.todo_id,
                           trigger_at=trigger_at, title=f'提醒：{todo.title}', status='pending',
                           source='web_native', sync_status='pending_ios' if sync_to_ios else 'local_only')
        db.add(pending)
        created = True
    else:
        created = False
    pending.trigger_at = trigger_at
    pending.title = f'提醒：{todo.title}'
    pending.schedule_id = schedule_id
    pending.schedule_offset_seconds = offset
    await db.flush()
    return pending, created


async def schedule_dependents(db, user, schedule_id):
    """Select only pending schedule-owned or explicitly relative reminders for this schedule."""
    return list(await db.scalars(select(Reminder).where(
        Reminder.user_id == user.user_id, Reminder.schedule_id == schedule_id,
        Reminder.status == 'pending', Reminder.deleted_at.is_(None),
        or_(Reminder.todo_id.is_(None), Reminder.schedule_offset_seconds.is_not(None)))))


async def refresh_schedule_reminders(db, user, schedule, reminders, *, cancelled=False):
    """Recompute explicit relative reminders, or cancel only dependent alerts; todo completion is untouched."""
    for reminder in reminders:
        if reminder.todo_id is None:
            reminder.title = f'提醒：{schedule.title}'
        if cancelled:
            reminder.status = 'cancelled'
            reminder.cancelled_at = datetime.now(UTC)
        elif reminder.schedule_offset_seconds is not None:
            reminder.trigger_at = ensure_aware(schedule.starts_at) + timedelta(seconds=reminder.schedule_offset_seconds)
        if reminder.todo_id and reminder.schedule_offset_seconds is not None:
            todo = await db.scalar(select(MatterTodo).where(
                MatterTodo.todo_id == reminder.todo_id, MatterTodo.user_id == user.user_id,
                MatterTodo.deleted_at.is_(None)))
            if todo is not None:
                todo.reminder_at = None if cancelled else reminder.trigger_at
    await db.flush()


async def sync_schedule_reminder(db, user, *, matter_id, schedule, trigger_at, offset_seconds=None, sync_to_ios=False):
    """Maintain a schedule-only absolute or explicitly relative reminder, independently of todos."""
    if offset_seconds is not None:
        trigger_at = ensure_aware(schedule.starts_at) + timedelta(seconds=offset_seconds)
    row = await db.scalar(select(Reminder).where(
        Reminder.user_id == user.user_id, Reminder.schedule_id == schedule.schedule_id,
        Reminder.todo_id.is_(None), Reminder.status == 'pending', Reminder.deleted_at.is_(None)))
    if trigger_at is None:
        if row:
            row.status = 'cancelled'
            row.cancelled_at = datetime.now(UTC)
        return row, False
    created = row is None
    if row is None:
        row = Reminder(user_id=user.user_id, matter_id=matter_id, schedule_id=schedule.schedule_id,
                       title=f'提醒：{schedule.title}', trigger_at=trigger_at, status='pending', source='web_native',
                       sync_status='pending_ios' if sync_to_ios else 'local_only')
        db.add(row)
    row.trigger_at = trigger_at
    row.schedule_offset_seconds = offset_seconds
    row.title = f'提醒：{schedule.title}'
    await db.flush()
    return row, created
