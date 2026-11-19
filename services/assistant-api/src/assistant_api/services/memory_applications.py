"""Validate model-selected business sources and retain encrypted, non-UI provenance.

No semantic selection, model request, setting, frontend entry or automatic
reapplication is performed here. Business services own the final transaction.
"""
import base64
from copy import deepcopy
from datetime import UTC, date, datetime

from sqlalchemy import select
from pydantic import BaseModel, ValidationError

from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import BusinessMemoryApplication, Matter, MatterTodo, ScheduleEvent, CommutePlan
from assistant_api.schemas.preferences import PreferenceResolveRequest
from assistant_api.services import preferences
from assistant_api.services.memory_repository import for_session

OPTIONS = {'use_preferences', 'preference_date', 'memory_defaults'}


def public_request(value):
    """Omit private selection inputs from preview metadata without changing stored replay/audit requests."""
    if isinstance(value, dict):
        return {key: public_request(part) for key, part in value.items() if key != 'memory_defaults'}
    if isinstance(value, list):
        return [public_request(part) for part in value]
    return value


def request_identity(request, *, exclude_unset=False):
    """Keep pre-existing replay payloads compatible by omitting only newly introduced unset options."""
    value = request.model_dump(mode='json', exclude_unset=exclude_unset)
    for key in OPTIONS | {'priority', 'estimated_duration_minutes', 'available_after', 'place', 'schedule_due'}:
        if key not in request.model_fields_set:
            value.pop(key, None)
    if request.__class__.__name__ == 'MemorySelection' and 'references' not in request.model_fields_set:
        value.pop('references', None)
    for key in list(value):
        part = getattr(request, key, None)
        if isinstance(part, BaseModel):
            value[key] = request_identity(part, exclude_unset=exclude_unset)
        elif isinstance(part, list):
            value[key] = [request_identity(item, exclude_unset=exclude_unset) if isinstance(item, BaseModel) else item for item in part]
        elif isinstance(part, dict) and any(isinstance(item, BaseModel) for item in part.values()):
            value[key] = {name: request_identity(item, exclude_unset=exclude_unset) if isinstance(item, BaseModel) else item for name, item in part.items()}
    return value


def json_values(value):
    """Convert verified business snapshots to JSON without changing their values or inventing missing data."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_values(part) for key, part in value.items()}
    if isinstance(value, list):
        return [json_values(part) for part in value]
    return value


async def reference_snapshot(db, user, reference):
    """Read an owned live business reference and require its actual parent version or plan revision."""
    identity = str(reference.record_id)
    if reference.kind == 'commute_plan':
        row = await db.scalar(select(CommutePlan).where(CommutePlan.user_id == user.user_id, CommutePlan.plan_id == identity))
        if row is None:
            raise NotFoundError('memory_reference_not_found', '所引用的业务对象不存在。')
        version = row.revision
        from assistant_api.commute.serializers import plan_read
        snapshot = plan_read(row).model_dump(mode='json')
    else:
        model, key = (ScheduleEvent, ScheduleEvent.schedule_id) if reference.kind == 'schedule' else (MatterTodo, MatterTodo.todo_id)
        row = await db.scalar(select(model).where(model.user_id == user.user_id, key == identity, model.deleted_at.is_(None)))
        parent = await db.get(Matter, row.matter_id) if row and row.matter_id else None
        if row is None or parent is None or parent.user_id != user.user_id or parent.deleted_at is not None:
            raise NotFoundError('memory_reference_not_found', '所引用的业务对象不存在或已失效。')
        version = parent.version
        snapshot = {key: getattr(row, key) for key in (('title', 'starts_at', 'ends_at', 'status', 'location',
                    'latitude', 'longitude', 'coordinate_system', 'location_city', 'location_address')
                    if reference.kind == 'schedule' else ('title', 'due_at', 'reminder_at', 'status'))}
    if version != reference.version:
        raise ConflictError('memory_reference_changed', '计算依据的业务版本已改变，尚未写入。')
    return {'kind': reference.kind, 'record_id': identity, 'version': version, 'snapshot': json_values(snapshot)}


async def resolve_input(db, user, kind, request, *, execution_run_id=None, excluded=()):
    """Resolve only explicitly submitted choices; unspecified fields and semantic relevance stay with the caller."""
    explicit = request.model_dump(mode='json', exclude_unset=True, exclude=OPTIONS | set(excluded))
    selections = request.memory_defaults
    preferences.validate_memory_sources(explicit, selections, enabled=request.use_preferences)
    if not selections:
        return explicit, None
    try:
        choice = PreferenceResolveRequest(purpose=kind, values=explicit, selections=selections, reference_date=request.preference_date)
    except ValidationError as exc:
        raise InvalidRequestError('memory_fields_invalid', '当前业务接口不支持这些记忆字段或参数，请按已提供定义修正；尚未写入。') from exc
    receipt = await preferences.resolve(db, None, user, choice, execution_run_id=execution_run_id)
    preferences.require_resolved(receipt)
    receipt['local_run_id'] = execution_run_id
    return receipt['values'], receipt


def pack(db, user, applications):
    """Encrypt pending exact receipts in a draft; frontend serialization omits this private envelope."""
    return base64.b64encode(for_session(db).encrypt(user.user_id, json_values(applications))).decode()


def unpack(db, user, payload):
    """Read only the server-created encrypted draft receipt, never a client-supplied analysis object."""
    encoded = (payload.get('analysis') or {}).get('memory_application_envelope')
    return for_session(db).decrypt(user.user_id, base64.b64decode(encoded)) if encoded else []


def equal_value(left, right):
    """Compare timestamp instants across JSON/SQL timezone normalization and other values without fingerprints."""
    if isinstance(left, (str, datetime)) and isinstance(right, (str, datetime)):
        try:
            a = datetime.fromisoformat(left.replace('Z', '+00:00')) if isinstance(left, str) else left
            b = datetime.fromisoformat(right.replace('Z', '+00:00')) if isinstance(right, str) else right
            return (a if a.tzinfo else a.replace(tzinfo=UTC)).astimezone(UTC) == (b if b.tzinfo else b.replace(tzinfo=UTC)).astimezone(UTC)
        except ValueError:
            pass
    return json_values(left) == json_values(right)


def assert_values(receipt, actual):
    """Refuse a source claim when the selected value no longer matches the actual business fields."""
    for field in receipt['fields']:
        keys = ({key for key in receipt['values'] if key.startswith('location') or key in ('latitude', 'longitude', 'coordinate_system')}
                if field == 'location' else {field})
        if any(not equal_value(receipt['values'].get(key), actual.get(key)) for key in keys):
            raise ConflictError('preferences_draft_changed', '实际保存字段与本次记忆选择不一致，尚未写入；请明确重新选择或提交普通值。')


async def verify(db, user, applications, payload):
    """Lock account memory writes and recheck versions, business inputs and current preview values before saving."""
    if not applications:
        return
    for application in applications:
        receipt = application['receipt']
        kind = application['kind']
        items = payload.get('todos') or []
        actual = (bundle_values(payload) if kind == 'commute_bundle' else payload.get('schedule') if kind == 'schedule'
                  else (items[application['index']] if application['index'] < len(items) else None))
        if actual is None:
            raise ConflictError('preferences_draft_changed', '记忆选择对应的业务提议已移除，尚未写入。')
        assert_values(receipt, actual)
        await verify_receipt(db, user, receipt)


async def verify_receipt(db, user, receipt):
    """Serialize memory mutations and recheck exact source/reference versions inside the business transaction."""
    from assistant_api.schemas.preferences import MemoryBusinessReference
    await for_session(db).lock_scope(db, user.user_id)
    for field, detail in receipt['fields'].items():
        await preferences.assert_current(db, None, user, {**receipt,
            'purpose': detail.get('application_purpose', receipt['purpose']), 'fields': {field: detail}})
        for reference in detail.get('references', []):
            current = await reference_snapshot(db, user, MemoryBusinessReference.model_validate({
                key: reference[key] for key in ('kind', 'record_id', 'version')}))
            if current != reference:
                raise ConflictError('memory_reference_changed', '计算依据的业务内容已改变，尚未写入。')


async def persist(db, user, application, *, command_key, target_id, target_revision, actual):
    """Append immutable encrypted provenance using the caller's transaction; never commit business data here."""
    assert_values(application['receipt'], actual)
    body = {**deepcopy(application), 'target_id': target_id, 'target_revision': target_revision}
    body['receipt']['application_status'] = 'applied'
    body['actual_values'] = {field: json_values(actual.get(field)) for field in application['receipt']['fields']}
    db.add(BusinessMemoryApplication(user_id=user.user_id, command_key=command_key,
        target_kind=application['kind'], target_id=target_id, target_revision=target_revision,
        body=for_session(db).encrypt(user.user_id, json_values(body))))


async def prepare_combination(db, user, request, *, execution_run_id=None):
    """Resolve nested schedule/todo choices separately and return formal fields plus private receipts."""
    from assistant_api.schemas.matters import SchedulePayload, TodoPayload
    values = request.model_dump(exclude={'schedule', 'todos'})
    applications = []
    for kind, items in (('schedule', [request.schedule] if request.schedule else []), ('todo', request.todos)):
        resolved = []
        for index, item in enumerate(items):
            fields, receipt = await resolve_input(db, user, kind, item, execution_run_id=execution_run_id)
            parsed = (SchedulePayload if kind == 'schedule' else TodoPayload).model_validate(fields)
            resolved.append(parsed.model_dump(mode='json'))
            if receipt:
                applications.append({'kind': kind, 'index': index, 'receipt': receipt, 'scope': 'creation'})
        values['schedule' if kind == 'schedule' else 'todos'] = (resolved[0] if resolved else None) if kind == 'schedule' else resolved
    return values, applications


async def prepare_patch(db, user, draft, request, source, *, execution_run_id=None):
    """Resolve explicit draft choices and remove pending claims superseded by ordinary edits, retaining applied history."""
    from assistant_api.schemas.matters import SchedulePayload, TodoPayload
    from assistant_api.schemas.preferences import MEMORY_INPUT_GROUPS
    changes = request.model_dump(exclude_unset=True, exclude={'expected_version'})
    applications = unpack(db, user, source)
    for kind, name in (('schedule', 'schedule'), ('todo', 'todos')):
        if name not in request.model_fields_set:
            continue
        items = ([request.schedule] if request.schedule else []) if kind == 'schedule' else (request.todos or [])
        old = {part['index']: part for part in applications if part['kind'] == kind}
        applications = [part for part in applications if part['kind'] != kind]
        resolved = []
        for index, item in enumerate(items):
            explicit = item.model_dump(mode='json', exclude_unset=True, exclude=OPTIONS)
            fields, receipt = await resolve_input(db, user, kind, item, execution_run_id=execution_run_id)
            parsed = (SchedulePayload if kind == 'schedule' else TodoPayload).model_validate(fields)
            resolved.append(parsed.model_dump(mode='json', exclude_unset=True))
            previous = deepcopy(old.get(index))
            if previous:
                previous['receipt']['fields'] = {field: detail for field, detail in previous['receipt']['fields'].items()
                    if not MEMORY_INPUT_GROUPS.get(field, {field}).intersection(explicit) and field not in item.memory_defaults}
                if previous['receipt']['fields']:
                    if receipt:
                        receipt['fields'] = {**previous['receipt']['fields'], **receipt['fields']}
                        receipt['values'] = {**previous['receipt']['values'], **receipt['values']}
                    else:
                        applications.append(previous)
            if receipt:
                applications.append({'kind': kind, 'index': index, 'receipt': receipt,
                    'scope': 'reapplication' if draft.kind == 'update' else 'creation'})
        changes[name] = (resolved[0] if resolved else None) if kind == 'schedule' else resolved
    return changes, applications


def bundle_values(payload):
    """Read the actual server-quoted commute conditions, never model-proposed route duration or coordinates."""
    state = payload.get('commute') or {}
    return {**state.get('intent', {}), **state.get('quote_inputs', {}), 'mode': state.get('selected_mode')}


def merge_bundle(applications, values, receipt):
    """Carry unchanged preview sources across quote phases and replace only explicitly changed field claims."""
    applications = deepcopy(applications)
    retained = [part for part in applications if part['kind'] != 'commute_bundle']
    fields, resolved = {}, {}
    scope = 'creation'
    for application in applications:
        if application['kind'] != 'commute_bundle':
            continue
        old = application['receipt']
        for field, detail in old['fields'].items():
            if field not in values or equal_value(values[field], old['values'].get(field)):
                fields[field] = detail
        resolved.update(old['values'])
        if receipt is None:
            receipt = {**old, 'fields': {}, 'values': {}}
    if receipt:
        fields.update(receipt['fields'])
        resolved.update(values)
        resolved.update(receipt['values'])
        receipt = {**receipt, 'fields': fields, 'values': resolved}
        if fields:
            retained.append({'kind': 'commute_bundle', 'index': 0, 'scope': scope, 'receipt': receipt})
    return retained
