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
