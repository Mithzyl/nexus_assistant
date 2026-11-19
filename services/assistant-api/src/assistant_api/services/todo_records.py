"""Todo record persistence independent of calendar creation and reminder storage.

The caller provides validated fields and owns the parent transaction. No calendar
record or reminder is created as a side effect of these functions.
"""
from datetime import UTC, datetime
from sqlalchemy import update
from assistant_api.models import MatterTodo, User
from assistant_api.core.errors import ConflictError, InvalidRequestError


async def save_todo(db, user: User, *, matter_id: str, values: dict, position: int,
                    existing: MatterTodo | None):
    """Save a todo's own fields and completion state without depending on a calendar record."""
    status = values.get('status', existing.status if existing else 'open')
    values = {**values, 'status': status,
              'completed_at': (existing.completed_at if existing and existing.status == 'done' else datetime.now(UTC)) if status == 'done' else None}
    if existing is None:
        row = MatterTodo(user_id=user.user_id, matter_id=matter_id, position=position, **values)
        db.add(row)
        await db.flush()
        return row
    if existing.user_id != user.user_id or existing.matter_id != matter_id:
        raise InvalidRequestError('todo_not_in_matter', '待办不属于当前事项。')
    result = await db.execute(update(MatterTodo).where(
        MatterTodo.todo_id == existing.todo_id, MatterTodo.user_id == user.user_id,
        MatterTodo.updated_at == existing.updated_at, MatterTodo.deleted_at.is_(None),
    ).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError('todo_changed', '该待办已被修改，请重新读取。')
    await db.refresh(existing)
    return existing


def planning_snapshot(todo):
    """Expose stored planning inputs without silently inventing values for legacy todos."""
    return {**{field: getattr(todo, field) for field in PLANNING_FIELDS},
            'schedule_due': ({'schedule_id': todo.due_schedule_id, 'offset_seconds': todo.due_schedule_offset_seconds}
                             if todo.due_schedule_id else None)}


PLANNING_FIELDS = ('priority', 'estimated_duration_minutes', 'available_after', 'place')


async def deadline_values(db, user, *, item, existing, matter_id, timezone, current_schedule):
    """Resolve a same-matter native schedule deadline inside the caller's write transaction."""
    from sqlalchemy import select
    from assistant_api.models import ScheduleEvent
    from assistant_api.services.matter_conflicts import can_edit_schedule
    from assistant_api.services.reminder_records import absolute_time
    from assistant_api.commute.timeutil import ensure_aware
    from datetime import timedelta

    previous = planning_snapshot(existing) if existing else {}
    result = {field: item.get(field, previous.get(field)) for field in PLANNING_FIELDS}
    result['available_after'] = absolute_time(result['available_after'], timezone)
    absolute = absolute_time(item.get('due_at'), timezone)
    relation = item.get('schedule_due')
    if 'schedule_due' not in item and existing and ('due_at' not in item or absolute == (ensure_aware(existing.due_at) if existing.due_at else None)):
        # Older draft clients may round-trip the calculated timestamp without knowing the dependency.
        relation = previous.get('schedule_due')
        if relation:
            absolute = None
    if relation is None:
        return {**result, 'due_at': absolute if 'due_at' in item else None if 'schedule_due' in item
                else existing.due_at if existing else None, 'due_schedule_id': None, 'due_schedule_offset_seconds': None}
    if absolute is not None:
        raise InvalidRequestError('deadline_timing_conflict', '绝对截止和相对截止只能选一种。')
    schedule_id = relation.get('schedule_id') or (current_schedule.schedule_id if current_schedule else None)
    if not schedule_id:
        raise InvalidRequestError('deadline_schedule_required', '请指定截止所依据的日程。')
    schedule = await db.scalar(select(ScheduleEvent).where(
        ScheduleEvent.schedule_id == schedule_id, ScheduleEvent.user_id == user.user_id,
        ScheduleEvent.matter_id == matter_id, ScheduleEvent.deleted_at.is_(None)))
    if schedule is None or not can_edit_schedule(schedule.source, schedule.data_source_id):
        raise InvalidRequestError('deadline_schedule_invalid', '相对截止只支持同一事项中由 Assistant 管理的日程。')
    offset = relation['offset_seconds']
    unchanged = previous.get('schedule_due') == {'schedule_id': schedule_id, 'offset_seconds': offset}
    if schedule.status != 'scheduled' and not unchanged:
        raise InvalidRequestError('deadline_schedule_inactive', '不能将截止时间绑定到已取消的日程。')
    return {**result, 'due_at': ensure_aware(schedule.starts_at) + timedelta(seconds=offset)
            if schedule.status == 'scheduled' else None, 'due_schedule_id': schedule_id,
            'due_schedule_offset_seconds': offset}


def refresh_deadline(todo, schedule, *, cancelled=False):
    """Recalculate only an explicitly bound deadline without changing either reminder mode."""
    from datetime import timedelta
    from assistant_api.commute.timeutil import ensure_aware
    todo.due_at = None if cancelled else ensure_aware(schedule.starts_at) + timedelta(seconds=todo.due_schedule_offset_seconds)
