"""Validate task-selected memories and preserve their actual use; legacy settings remain compatible.

The Agent selects relevance. Assistant verifies ownership, versions, explicit
restrictions, parameter schemas and attribution before business persistence.
"""
from copy import deepcopy
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from assistant_api.core.errors import ConflictError, InvalidRequestError
from assistant_api.models import UserProfile
from assistant_api.nexus_client.errors import NexusError, NexusProtocolError
from assistant_api.schemas.preferences import PreferenceRead, PreferenceResolveRequest
from assistant_api.services import business_memory

NAMESPACE = 'business_preferences'


def initial_controls():
    """Return the retained legacy settings shape; new task selections do not consult these switches."""
    return {'version':0, 'purposes':{'schedule':False,'route':False,'reminder':False},
            'bindings':{},'defaults':{},'receipts':[]}


async def controls(db, user):
    """Read legacy controls for compatibility and old pending drafts, preserving all unrelated keys."""
    profile=await db.scalar(select(UserProfile).where(UserProfile.user_id==user.user_id)
        .execution_options(populate_existing=True))
    raw=deepcopy(profile.preferences_json or {}) if profile else {}
    settings=raw.get(NAMESPACE,initial_controls())
    # Invalid stored controls must not silently turn into enabled defaults.
    try:
        PreferenceRead.model_validate(settings)
    except ValidationError as exc:
        raise ConflictError('preferences_invalid','偏好配置结构无法确认，请先修正配置。') from exc
    return profile,raw,deepcopy(settings)


async def read(db, user):
    """Return the authenticated owner's settings without stored request receipts."""
    _,_,settings=await controls(db,user)
    return PreferenceRead.model_validate(settings).model_dump(mode='json')


async def current_memory(client, user_id, entry_id, version):
    """Read an owned exact entry through the Assistant store; stale or forgotten facts are unusable."""
    history=await client.list_application_memory(user_id,history_entry_id=entry_id,limit=1)
    items=history.get('items')
    if (history.get('entry_id')!=entry_id or type(history.get('current_version')) is not int
            or not isinstance(items,list) or not items or not isinstance(items[0],dict)
            or items[0].get('version')!=history['current_version']):
        raise ConflictError('memory_unconfirmed','无法确认该记忆的当前版本。')
    item=items[0]
    if item.get('status')!='active' or history['current_version']!=version:
        raise ConflictError('memory_changed','该记忆已修改、撤回或尚未确认，请重新选择。')
    return item


async def patch(db, client, user, payload):
    """Persist legacy owner settings for compatibility without controlling new memory selections.

The latest 32 settings requests can replay their saved response. Older requests
with the original version fail with 409 instead of performing another update.
No memory text is copied, no upstream settings are widened, and no migration is needed.
"""
    from assistant_api.services.memory_repository import for_session
    client=for_session(db)
    profile,raw,settings=await controls(db,user)
    command=payload.model_dump(mode='json')
    for receipt in settings.get('receipts',[]):
        if receipt['key']==payload.idempotency_key:
            if receipt['request']!=command:
                raise ConflictError('idempotency_key_reused','该请求标识已用于另一项设置。')
            return {**receipt['result'],'replayed':True}
    if settings['version']!=payload.expected_version:
        raise ConflictError('preferences_changed','偏好已修改，请重新读取。')
    bindings=dict(settings['bindings'])
    if payload.bindings:
        # Management can remove a binding while memory use is disabled.
        for role,binding in payload.bindings.items():
            if binding is None:
                bindings.pop(role,None)
            else:
                await current_memory(client,user.user_id,str(binding.entry_id),binding.version)
                bindings[role]=binding.model_dump(mode='json')
    if len(bindings)>32:
        raise InvalidRequestError('preferences_too_many_bindings','最多保留 32 个默认角色绑定。')
    defaults={**settings['defaults'],**payload.defaults}
    if any(role not in bindings for fields in defaults.values() for role in fields.values()):
        raise InvalidRequestError('preference_role_missing','默认字段引用的角色必须存在；删除角色前请同时移除默认字段。')
    result={'version':settings['version']+1,'purposes':{**settings['purposes'],**payload.purposes},
            'bindings':bindings,'defaults':defaults,'replayed':False}
    receipts=[*settings.get('receipts',[]),{'key':payload.idempotency_key,'request':command,'result':result}][-32:]
    updated={**raw,NAMESPACE:{**result,'receipts':receipts}}
    if profile is None:
        db.add(UserProfile(user_id=user.user_id,preferences_json=updated))
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError('preferences_changed','偏好已修改，请重新读取。') from exc
    else:
        changed=await db.execute(update(UserProfile).where(UserProfile.user_id==user.user_id,
            UserProfile.preferences_json==raw).values(preferences_json=updated).execution_options(synchronize_session=False))
        if changed.rowcount!=1:
            await db.rollback()
            raise ConflictError('preferences_changed','偏好或其他个人设置已修改，请重新读取。')
        await db.commit()
        await db.refresh(profile)
    return result


def business_time(request, timezone):
    """Read supplied schedule, todo or commute dates; never turn a habitual time into a new command."""
    for name in ('starts_at','due_at','reminder_at','available_after','depart_at','arrive_by','effective_from'):
        stamp=request.values.get(name)
        if isinstance(stamp,str):
            try:
                value=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(timezone))
            except (ValueError,ZoneInfoNotFoundError):
                continue
    return None


def applicable_selection(item, request, timezone, *, at=None):
    """Check explicit memory restrictions now and at the selected business date; never infer a domain restriction."""
    projection=business_memory.business_projection(item)
    if not business_memory.applicable(projection,request.purpose,timezone,request.matter_id):
        return False
    target=at or business_time(request,timezone)
    if target is None and request.reference_date:
        rules=projection.get('applicability',{})
        if any(isinstance(rules.get(key),str) and len(rules[key])>10
               for key in ('effective_from','effective_until')):
            return False
        target=datetime.combine(request.reference_date,time.min,ZoneInfo(timezone))
    return target is None or business_memory.applicable(projection,request.purpose,timezone,request.matter_id,at=target)


def selected_value(item, field, selection, request, timezone):
    """Validate a proposed field and verify structured facts; free-text interpretation remains explicitly model/user-authored.

    Semantic relevance is the Agent's decision. This function checks schema,
    explicit dates/weekdays, real coordinate systems and known structured facts;
    it never pretends a deterministic check can prove a free-text interpretation.
    """
    from assistant_api.schemas.matters import Coordinate, SchedulePayload
    value=selection.value
    typed=item.get('typed_data') or {}
    projection=business_memory.business_projection(item)
    stored=projection.get('value',{})
    if not isinstance(typed,dict) or not isinstance(stored,dict):
        raise ValueError('structured_value_invalid')
    if request.purpose in ('commute_plan', 'commute_draft', 'commute_quote'):
        from pydantic import TypeAdapter
        from assistant_api.commute.schemas import CommutePlanInput, CommuteLocation
        if field in ('origin', 'destination'):
            place = stored.get('place', typed if typed.get('memory_type') == 'place' else None)
            if not isinstance(place, dict):
                raise ValueError('verified_coordinates_required')
            expected = CommuteLocation(label=place.get('label'), latitude=place.get('latitude'), longitude=place.get('longitude')).model_dump(mode='json')
            crs = place.get('coordinate_system')
            if crs not in ('GCJ02', 'gcj02') or value != expected:
                raise ValueError('structured_value_mismatch')
            return expected, 'structured_memory'
        from assistant_api.services.commute_bundle import CommuteIntent, QuoteValues
        model = CommutePlanInput if request.purpose == 'commute_plan' else CommuteIntent if request.purpose == 'commute_draft' else QuoteValues
        value = TypeAdapter(model.model_fields[field].annotation).validate_python(value)
        known = projection.get('transport_mode') if field in ('transport_mode', 'mode') else stored.get(field)
        if known is not None and known != value:
            raise ValueError('structured_value_mismatch')
        return value, 'structured_memory' if known is not None else 'interpretation'
    if request.purpose == 'todo' and field in ('due_at', 'reminder_at'):
        if value is None:
            return None, 'interpretation'
        if not isinstance(value, str):
            raise ValueError('time_value_invalid')
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('aware_time_required')
        if field in stored and stored[field] != value:
            raise ValueError('structured_value_mismatch')
        return stamp.isoformat(), 'structured_memory' if field in stored else 'interpretation'
    if request.purpose == 'todo':
        from assistant_api.schemas.matters import TodoPlanningFields, TodoPlace, TodoScheduleDue
        if field == 'available_after':
            if value is not None:
                if not isinstance(value, str) or datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError('aware_time_required')
        if field == 'place' and value is not None:
            candidate = TodoPlace.model_validate(value)
            stored_place = stored.get('place', typed if typed.get('memory_type') == 'place' else None)
            if isinstance(stored_place, dict):
                expected = {key: val for key, val in stored_place.items() if key in TodoPlace.model_fields}
                if expected.get('coordinate_system'):
                    expected['coordinate_system'] = expected['coordinate_system'].lower()
                if candidate != TodoPlace.model_validate(expected):
                    raise ValueError('structured_value_mismatch')
                return candidate.model_dump(mode='json'), 'structured_memory'
            if candidate.latitude is not None:
                raise ValueError('verified_coordinates_required')
        if field == 'schedule_due' and value is not None and not TodoScheduleDue.model_validate(value).schedule_id:
            raise ValueError('business_value_invalid')
        # Validate constraints on the model as OpenAPI does, not only its Python annotation.
        normalized = TodoPlanningFields.model_validate({field:value}).model_dump(mode='json')[field]
        if field in stored:
            known = TodoPlanningFields.model_validate({field:stored[field]}).model_dump(mode='json')[field]
            if known != normalized:
                raise ValueError('structured_value_mismatch')
        return normalized, 'structured_memory' if field in stored else 'interpretation'
    if request.purpose == 'availability' and field == 'windows':
        from pydantic import TypeAdapter
        from assistant_api.schemas.next_actions import WeeklyWindow
        from typing import Annotated
        from pydantic import Field
        rules = projection.get('applicability', {})
        known_timezone = stored.get('timezone', typed.get('timezone'))
        if known_timezone and known_timezone != request.values.get('timezone'):
            raise ValueError('structured_value_mismatch')
        if set(rules)-{'purposes'} or stored.get('weekdays') or typed.get('weekdays'):
            raise ValueError('business_value_invalid')
        adapter = TypeAdapter(Annotated[list[WeeklyWindow], Field(min_length=1, max_length=28)])
        normalized = adapter.dump_python(adapter.validate_python(value), mode='json')
        if 'windows' in stored and normalized != adapter.dump_python(adapter.validate_python(stored['windows']),mode='json'):
            raise ValueError('structured_value_mismatch')
        return normalized, 'structured_memory' if 'windows' in stored else 'interpretation'
    weekdays=stored.get('weekdays',typed.get('weekdays'))
    if weekdays is not None:
        if not request.reference_date:
            raise ValueError('reference_date_required')
        if (not isinstance(weekdays,list) or not weekdays
                or any(type(day) is not int or not 1<=day<=7 for day in weekdays)
                or request.reference_date.isoweekday() not in weekdays):
            raise ValueError('weekday_not_applicable')
    if field in {'location','origin','destination'}:
        place=stored.get('place',typed if typed.get('memory_type')=='place' else None)
        if value is None and field=='location':
            return {'location':None},'interpretation'
        proposed={'location':value} if isinstance(value,str) and field=='location' else value
        if not isinstance(proposed,dict):
            raise ValueError('place_value_invalid')
        keys={'location','location_address','location_city','latitude','longitude','coordinate_system','location_source'}
        if field!='location':
            keys={'latitude','longitude','coordinate_system','city'}
        if set(proposed)-keys:
            raise ValueError('place_value_invalid')
        if isinstance(place,dict):
            crs={'GCJ02':'gcj02','WGS84':'wgs84','BD09':'bd09'}.get(place.get('coordinate_system'),place.get('coordinate_system'))
            if field=='location':
                expected={'location':place.get('label'),'location_address':place.get('address'),
                    'latitude':place.get('latitude'),'longitude':place.get('longitude'),
                    'coordinate_system':crs,'location_city':place.get('city'),'location_source':'memory'}
                candidate=SchedulePayload(title='地点校验',starts_at=datetime.now(UTC),**expected)
                expected={key:getattr(candidate,key) for key in expected}
            else:
                if crs!='gcj02':
                    raise ValueError('coordinate_system_unsupported')
                expected=Coordinate(latitude=place.get('latitude'),longitude=place.get('longitude'),
                    coordinate_system=crs,city=place.get('city')).model_dump(mode='json')
            if any(expected.get(key)!=part for key,part in proposed.items()):
                raise ValueError('structured_value_mismatch')
            return expected,'structured_memory'
        # Textual memories can name a place, but cannot prove newly invented coordinates.
        if field!='location' or any(proposed.get(key) is not None for key in ('latitude','longitude')):
            raise ValueError('verified_coordinates_required')
        if proposed.get('coordinate_system','unknown')!='unknown':
            raise ValueError('verified_coordinates_required')
        if not any(isinstance(proposed.get(key),str) and proposed[key] and proposed[key] in selection.source_quote
                   for key in ('location','location_address')):
            raise ValueError('place_quote_mismatch')
        candidate=SchedulePayload(title='地点校验',starts_at=datetime.now(UTC),**proposed)
        result={key:getattr(candidate,key) for key in proposed}
        result['location_source']='memory'
        return result,'interpretation'
    if field=='mode':
        if not isinstance(value,str) or value not in business_memory.MODES.values():
            raise ValueError('transport_value_invalid')
        known=projection.get('transport_mode')
        if known and known!=value:
            raise ValueError('structured_value_mismatch')
        return value,'structured_memory' if known else 'interpretation'
    if field=='reminder_offset_seconds':
        if value is not None and (type(value) is not int or not -31536000<=value<=31536000):
            raise ValueError('reminder_value_invalid')
        if field in stored:
            if stored[field]!=value or (stored[field] is not None and type(stored[field]) is not int):
                raise ValueError('structured_value_mismatch')
            return value,'structured_memory'
        return value,'interpretation'
    if field=='starts_at':
        if not request.reference_date or not isinstance(value,str):
            raise ValueError('reference_date_required')
        try:
            stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('aware_time_required')
            if stamp.astimezone(ZoneInfo(timezone)).date()!=request.reference_date:
                raise ValueError('reference_date_mismatch')
            local_time=stored.get('start_local_time',typed.get('preferred_time'))
            if local_time is not None:
                clock=time.fromisoformat(local_time)
                zone=ZoneInfo(stored.get('timezone',typed.get('timezone',timezone)))
                local=stamp.astimezone(zone)
                if (clock.tzinfo or local.time()!=clock or local.fold
                        or local.utcoffset()!=local.replace(fold=1).utcoffset()):
                    raise ValueError('structured_value_mismatch')
                return stamp.isoformat(),'structured_memory'
            return stamp.isoformat(),'interpretation'
        except (TypeError,ZoneInfoNotFoundError) as exc:
            raise ValueError('time_value_invalid') from exc
    raise ValueError('unsupported_field')


async def task_sources(db, user, execution_run_id):
    """Use only sources the current server-bound Agent actually retrieved; never accept caller-provided identity."""
    if not execution_run_id:
        return None
    from assistant_api.models import NexusRun
    root=await db.scalar(select(NexusRun).where(NexusRun.user_id==user.user_id,NexusRun.local_run_id==execution_run_id))
    if root is None or root.status not in {'pending','running'}:
        raise ConflictError('preference_task_unavailable','当前助手任务已停止，尚未执行依赖记忆的新操作。')
    return {(str(source.get('entry_id')),source.get('version'))
        for source in (root.context_json or {}).get('api_flow',{}).get('memory_sources',[]) if isinstance(source,dict)}


def validate_memory_sources(values, selections, *, enabled=True):
    """Reject ambiguous parameter sources before lookup or writes; never infer the user's intended source."""
    from assistant_api.schemas.preferences import MEMORY_INPUT_GROUPS
    from assistant_api.core.errors import InvalidRequestError
    if selections and not enabled:
        raise InvalidRequestError('input_source_conflict',
            'use_preferences=false 时不能提交 memory_defaults；请由当前任务选择是否使用记忆。未执行业务写入。')
    for field in selections:
        overlaps=sorted(MEMORY_INPUT_GROUPS.get(field,{field}) & values.keys())
        if overlaps:
            raise InvalidRequestError('input_source_conflict',
                f'{", ".join(overlaps)} 与 memory_defaults.{field} 不能同时提供（包括 null）。'
                '请根据用户本次输入只保留明确参数或记忆选择之一；采用记忆时省略对应普通字段。未执行业务写入。')


async def resolve(db, client, user, request, *, execution_run_id=None):
    """Validate task-selected fields and real business references without consulting legacy role settings."""
    validate_memory_sources(request.values,request.selections)
    from assistant_api.services.memory_repository import for_session
    client=for_session(db)
    if request.matter_id:
        from assistant_api.services.matters import _get_matter
        await _get_matter(db,user,request.matter_id)
    profile=await db.get(UserProfile,user.user_id)
    timezone=profile.timezone if profile else 'Asia/Shanghai'
    at=business_time(request,timezone)
    if at is not None and not request.reference_date:
        request=request.model_copy(update={'reference_date':at.astimezone(ZoneInfo(timezone)).date()})
    values=deepcopy(request.values)
    fields={}
    available_sources=await task_sources(db,user,execution_run_id) if request.selections else None
    selected_by='ai' if execution_run_id else 'user'
    remote_enabled=None
    cache={}
    for field,selection in request.selections.items():
        detail={'source':'unresolved','status':'unused','entry_id':str(selection.entry_id),
            'version':selection.version,'source_quote':selection.source_quote,
            'selection_reason':selection.reason,'selected_by':selected_by,'proposed_value':selection.value,
            'application_purpose': request.purpose}
        fields[field]=detail
        identity=(str(selection.entry_id),selection.version)
        if available_sources is not None and identity not in available_sources:
            detail['reason']='memory_not_retrieved'
            continue
        try:
            if remote_enabled is None:
                settings=await client.application_memory_settings(user.user_id)
                if type(settings.get('enabled')) is not bool:
                    raise NexusProtocolError('记忆启用状态无法确认。')
                remote_enabled=settings['enabled']
            if not remote_enabled:
                detail['reason']='memory_disabled'
                continue
            if identity not in cache:
                cache[identity]=await current_memory(client,user.user_id,*identity)
            item=cache[identity]
            if not isinstance(item.get('text'),str) or selection.source_quote not in item['text']:
                detail['reason']='source_quote_mismatch'
                continue
            value,origin=selected_value(item,field,selection,request,timezone)
            from assistant_api.services.memory_applications import reference_snapshot
            detail['references'] = [await reference_snapshot(db, user, reference) for reference in selection.references]
            target=datetime.fromisoformat(value) if field in {'starts_at','available_after'} and value else at
            if not applicable_selection(item,request,timezone,at=target):
                detail['reason']='not_applicable'
                continue
            if field=='location':
                values.update(value)
            else:
                values[field]=value
            detail.update(source='memory',status='applied',reason='applied',
                value_origin=origin if origin=='structured_memory' else 'model_interpretation' if execution_run_id else 'user_interpretation')
        except ConflictError as exc:
            detail['reason']=exc.code
        except NexusError as exc:
            detail.update(reason='memory_unavailable',error_code=exc.code)
        except ValidationError:
            detail['reason']='business_value_invalid'
        except ValueError as exc:
            # Only bounded internal validation codes are returned; never an upstream body.
            code=str(exc)
            detail['reason']=code if code in {'structured_value_invalid','reference_date_required','weekday_not_applicable',
                'place_value_invalid','coordinate_system_unsupported','structured_value_mismatch','verified_coordinates_required',
                'place_quote_mismatch','transport_value_invalid','reminder_value_invalid','aware_time_required',
                'reference_date_mismatch','time_value_invalid','unsupported_field'} else 'business_value_invalid'
    return {'preferences_version':None,'selection_mode':'model','purpose':request.purpose,'timezone':timezone,
            'values':values,'fields':fields,'application_status':'resolved','matter_id':request.matter_id,
            'reference_date':request.reference_date.isoformat() if request.reference_date else None}


def require_resolved(receipt):
    """Stop before business persistence when a chosen memory is unusable; the original Agent can ask for a choice."""
    missing={name:detail['reason'] for name,detail in receipt['fields'].items() if detail.get('status')=='unused'}
    if missing:
        raise ConflictError('preference_selection_unresolved','选中的记忆尚不可用于本次操作，请补查或 ask_user：'+
            '；'.join(name+': '+reason for name,reason in missing.items()))


async def assert_current(db, client, user, receipt, *, schedule=None):
    """Recheck chosen sources and compare only the formal fields of pending schedule drafts."""
    from assistant_api.services.memory_repository import for_session
    client=for_session(db)
    sources=[{'entry_id':field['entry_id'],'version':field['version']} for field in receipt['fields'].values()
             if field.get('source')=='memory' and field.get('status')=='applied']
    if not sources:
        return
    if schedule is not None:
        from assistant_api.schemas.matters import SchedulePayload
        from assistant_api.services.memory_applications import OPTIONS

        # Earlier direct drafts could store request-only memory options inside schedule.
        schedule = {key: value for key, value in schedule.items() if key not in OPTIONS}
        original=SchedulePayload.model_validate(receipt['values'])
        actual=SchedulePayload.model_validate(schedule)
        for field,detail in receipt['fields'].items():
            if detail.get('source')!='memory' or detail.get('status')!='applied':
                continue
            keys=({'location','location_address','location_city','latitude','longitude','coordinate_system','location_source'}
                  if field=='location' else {field})
            if any(getattr(original,key)!=getattr(actual,key) for key in keys):
                raise ConflictError('preferences_draft_changed','记忆来源与预览实际字段不一致，尚未保存；请用明确的新值重新提交。')
    if receipt.get('selection_mode')!='model':
        # Preserve the meaning of already-saved or pending legacy receipts.
        _,_,settings=await controls(db,user)
        if settings['version']!=receipt['preferences_version']:
            raise ConflictError('preferences_changed','旧预览使用的偏好配置已改变，请重新处理。')
    await business_memory.validate_sources(client,user.user_id,sources)
    if receipt.get('selection_mode')=='model':
        request=PreferenceResolveRequest(purpose=receipt['purpose'],values=schedule or receipt['values'],
            matter_id=receipt.get('matter_id'),reference_date=receipt.get('reference_date'))
        for source in sources:
            item=await current_memory(client,user.user_id,source['entry_id'],source['version'])
            if not applicable_selection(item,request,receipt['timezone']):
                raise ConflictError('preference_selection_unresolved','所用记忆已不适用于当前事项或日期，尚未保存。')
