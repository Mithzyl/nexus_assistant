"""Complete standalone calendar/todo operations using the shared draft transaction.

Controllers expose one business command to Web and AI. Internally an owned
preview supplies the existing version fencing, history and atomic save; users do
not need to create a parent, open a draft or confirm a second time.
"""
from datetime import UTC, datetime
from sqlalchemy import select
from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import MatterDraft, MatterTodo, Reminder, ScheduleEvent
from assistant_api.schemas import matters as schemas
from assistant_api.services import matters


async def owned_record(db, user, kind, record_id, *, include_deleted=False):
    """Read one owned application record; normal lookups omit soft-deleted records."""
    model, key = (MatterTodo, MatterTodo.todo_id) if kind == 'todo' else (ScheduleEvent, ScheduleEvent.schedule_id)
    stmt = select(model).where(key == record_id, model.user_id == user.user_id)
    if not include_deleted:
        stmt = stmt.where(model.deleted_at.is_(None))
    row = await db.scalar(stmt)
    if row is None:
        raise NotFoundError(f'{kind}_not_found', '待办不存在。' if kind == 'todo' else '日程不存在。')
    if row.matter_id:
        await matters._get_matter(db, user, row.matter_id)
    return row


async def read_record(db, user, kind, record_id, *, include_deleted=False):
    """Return an actual record, parent version and its own reminders for immediate reuse or display."""
    row = await owned_record(db, user, kind, record_id, include_deleted=include_deleted)
    relation = Reminder.todo_id == record_id if kind == 'todo' else (Reminder.schedule_id == record_id) & Reminder.todo_id.is_(None)
    reminders = list(await db.scalars(select(Reminder).where(
        Reminder.user_id == user.user_id, relation, Reminder.deleted_at.is_(None))))
    matter = await matters._get_matter(db, user, row.matter_id) if row.matter_id else None
    result = {kind: matters._serialize_todo(row) if kind == 'todo' else matters._serialize_schedule(row),
              'reminders': [matters._serialize_reminder(item) for item in reminders]}
    if matter:
        result['matter'] = (await matters._serialize_matters(db, [matter]))[0]
    return result


async def _saved_result(db, runtime, settings, user, kind, draft, key, record_id=None):
    """Apply the existing atomic transaction and return saved resources without another AI GET."""
    result = await matters.confirm_draft(db, runtime, settings, user, draft['draft_id'],
                                         schemas.ConfirmRequest(expected_version=draft['version']), key)
    if not result.get('applied', True):
        return {name: result[name] for name in ('matter', 'schedule', 'reminders', 'change_id', 'replayed', 'applied', 'conflict') if name in result}
    if record_id is None:
        record_id = result['todos'][0]['todo_id'] if kind == 'todo' else result['schedule']['schedule_id']
    value = await read_record(db, user, kind, record_id, include_deleted=True)
    return {**value, 'change_id': result['change_id'], 'replayed': result['replayed'], 'applied': True}


async def create_record(db, runtime, settings, user, kind, request):
    """Prepare exactly the requested resource and save it, reusing the parent creation transaction."""
    parent = {'matter_id': request.matter_id, 'expected_matter_version': request.expected_matter_version}
    fields = request.model_dump(exclude={'idempotency_key', 'matter_id', 'expected_matter_version'})
    payload = schemas.StructuredDraftRequest(
        idempotency_key=request.idempotency_key, **parent,
        title=request.title if request.matter_id is None else None,
        **({'todos': [fields]} if kind == 'todo' else {'schedule': fields}))
    draft = await matters.create_structured_draft(db, runtime, settings, user, payload)
    return await _saved_result(db, runtime, settings, user, kind, draft, request.idempotency_key)


async def edit_record(db, runtime, settings, user, kind, record_id, request, *, cancel=False):
    """Patch the same owned record using actual snapshots, retaining field-level independence and replay identity."""
    original = {'kind': kind, 'record_id': record_id, 'cancel': cancel, 'request': request.model_dump(mode='json')}
    existing = await db.scalar(select(MatterDraft).where(
        MatterDraft.user_id == user.user_id, MatterDraft.origin_key == request.idempotency_key))
    if existing is not None and (existing.payload_json or {}).get('analysis', {}).get('direct_request'):
        if existing.payload_json['analysis']['direct_request'] != original:
            raise ConflictError('idempotency_key_reused', '该请求标识已用于另一项操作。')
        return await _saved_result(db, runtime, settings, user, kind, matters._serialize_draft(existing), request.idempotency_key, record_id)
    row = await owned_record(db, user, kind, record_id)
    if not row.matter_id:
        raise InvalidRequestError('record_not_editable', '设备导入的记录不能在应用内改写。')
    matter = await matters._get_matter(db, user, row.matter_id)
    if matter.version != request.expected_matter_version:
        raise ConflictError('matter_changed', '该事项已被修改，请重新读取。')
    draft = await matters.create_edit_draft(db, runtime, settings, user, row.matter_id,
        schemas.MatterEditRequest(idempotency_key=request.idempotency_key,
                                  **({'todo_id': record_id} if kind == 'todo' else {'schedule_id': record_id})))
    stored = await matters._get_draft(db, user, draft['draft_id'])
    body = dict(stored.payload_json or {})
    body['analysis'] = {**body.get('analysis', {}), 'direct_request': original}
    stored.payload_json = body
    if cancel:
        if kind != 'schedule':
            raise InvalidRequestError('unsupported_cancellation', '请选择日程取消操作。')
        body['schedule'] = None
        body['schedule_cancellations'] = [{'schedule_id': record_id, 'selected': True, 'cancellable': True,
                                           'base_updated_at': matters._jsonable(row.updated_at)}]
        body['changes_note'] = '取消日程'
        stored.payload_json = body
        stored.conflict_json = None
        stored.version += 1
        await db.commit()
        draft = matters._serialize_draft(stored)
    else:
        changes = request.model_dump(exclude_unset=True, exclude={'idempotency_key', 'expected_matter_version'})
        if any(changes.get(field, 'omitted') is None for field in ('title', 'starts_at', 'status', 'coordinate_system')):
            raise InvalidRequestError('record_field_null', '必填字段不能设置为空。')
        if kind == 'todo':
            merged = {**body['todos'][0], **changes}
            if 'reminder_at' in changes and changes['reminder_at'] is not None:
                merged['schedule_reminder'] = None
            if changes.get('schedule_reminder') is not None:
                merged['reminder_at'] = None
            patch = schemas.DraftPatchRequest(expected_version=stored.version, todos=[schemas.TodoPayload.model_validate(merged)])
        else:
            merged = {**body['schedule'], **changes}
            if changes.get('reminder_at') is not None:
                merged['reminder_offset_seconds'] = None
            if changes.get('reminder_offset_seconds') is not None:
                merged['reminder_at'] = None
            patch = schemas.DraftPatchRequest(expected_version=stored.version,
                                              schedule=schemas.SchedulePayload.model_validate(merged))
        draft = await matters.patch_draft(db, runtime, settings, user, stored.draft_id, patch)
    return await _saved_result(db, runtime, settings, user, kind, draft, request.idempotency_key, record_id)


async def list_records(db, user, kind, *, matter_id=None, query=None, limit=30):
    """Find owned records by explicit title criteria, without requiring the caller to know a parent ID."""
    model = MatterTodo if kind == 'todo' else ScheduleEvent
    stmt = select(model).where(model.user_id == user.user_id, model.deleted_at.is_(None))
    if matter_id:
        await matters._get_matter(db, user, matter_id)
        stmt = stmt.where(model.matter_id == matter_id)
    if query:
        stmt = stmt.where(model.title.contains(query, autoescape=True))
    rows = list(await db.scalars(stmt.order_by(model.created_at.desc()).limit(limit)))
    # Parent deletion is authoritative even for legacy imported child records.
    from assistant_api.models import Matter
    visible = []
    for row in rows:
        parent = await db.get(Matter, row.matter_id) if row.matter_id else None
        if row.matter_id and (parent is None or parent.deleted_at is not None):
            continue
        visible.append(matters._serialize_todo(row) if kind == 'todo' else matters._serialize_schedule(row))
    return {'items': visible}
