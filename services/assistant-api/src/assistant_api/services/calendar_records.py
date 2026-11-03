"""Calendar record persistence used by standalone commands and composed draft saves.

These functions do not create todos or commit transactions. The caller owns
parent-version validation, the surrounding transaction and reminder updates.
"""
from sqlalchemy import update
from assistant_api.models import ScheduleEvent, User
from assistant_api.core.errors import ConflictError, InvalidRequestError
from assistant_api.services.matter_conflicts import can_edit_schedule


async def save_schedule(db, user: User, *, matter_id: str, values: dict, existing: ScheduleEvent | None):
    """Insert a calendar record or conditionally update the same owned writable record."""
    if existing is None:
        row = ScheduleEvent(user_id=user.user_id, matter_id=matter_id, status='scheduled', **values)
        db.add(row)
        await db.flush()
        return row
    if existing.user_id != user.user_id or existing.matter_id != matter_id:
        raise InvalidRequestError('schedule_not_in_matter', '日程不属于当前事项。')
    if not can_edit_schedule(existing.source, existing.data_source_id):
        raise InvalidRequestError('schedule_not_editable', '设备导入的日程不能在应用内改写。')
    result = await db.execute(update(ScheduleEvent).where(
        ScheduleEvent.schedule_id == existing.schedule_id, ScheduleEvent.user_id == user.user_id,
        ScheduleEvent.updated_at == existing.updated_at, ScheduleEvent.deleted_at.is_(None),
    ).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise ConflictError('schedule_changed', '关联日程已变化，请重新读取。')
    await db.refresh(existing)
    return existing
