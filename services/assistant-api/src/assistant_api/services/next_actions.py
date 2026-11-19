"""Rank owned Todo and calendar records locally; recommendation feedback never edits records."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert

from assistant_api.commute.timeutil import ensure_aware, resolve_local_minutes
from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import Matter, MatterTodo, NextActionCommandReceipt, NextActionState, ScheduleEvent, UserAvailability
from assistant_api.models.base import new_id
from assistant_api.schemas.next_actions import AvailabilityRead, NextActionRead, NextActionReceipt
from assistant_api.services import memory_applications
from assistant_api.services.matter_conflicts import can_edit_schedule
from assistant_api.services.next_action_departures import saved_departures
from assistant_api.services.matters import user_timezone

PRIORITY = {'urgent': 0, 'high': 1, 'normal': 2, 'low': 3, None: 2}
SNAPSHOT_TTL = timedelta(minutes=5)


def server_now():
    """Use service time, never a client- or model-supplied clock."""
    return datetime.now(UTC)


async def read_availability(db, user, default_timezone):
    """Read saved work windows without reading or applying a new memory."""
    row = await db.get(UserAvailability, user.user_id)
    if row is None:
        return AvailabilityRead(version=0, timezone=await user_timezone(db, user, default_timezone), windows=[])
    return AvailabilityRead(version=row.version, timezone=row.timezone, windows=row.windows)


async def save_availability(db, user, payload, *, execution_run_id):
    """Replace versioned windows and persist any explicit memory claim in the same transaction."""
    values, memory = await memory_applications.resolve_input(db, user, 'availability', payload,
        execution_run_id=execution_run_id, excluded={'expected_version'})
    result = AvailabilityRead(version=payload.expected_version+1, **values)
    if memory:
        await memory_applications.verify_receipt(db, user, memory)
    fields = result.model_dump(mode='json')
    if payload.expected_version == 0:
        stmt = insert(UserAvailability).values(user_id=user.user_id, **fields).on_conflict_do_nothing()
    else:
        stmt = update(UserAvailability).where(UserAvailability.user_id == user.user_id,
            UserAvailability.version == payload.expected_version).values(**fields)
    if (await db.execute(stmt)).rowcount != 1:
        raise ConflictError('availability_changed', '作息已变化，请读取当前版本后重试。')
    if memory:
        await memory_applications.persist(db, user, {'kind':'availability','receipt':memory,'scope':'reapplication'},
            command_key=f'availability:{result.version}', target_id=user.user_id, target_revision=result.version,
            actual=fields)
    await db.commit()
    return result


def _minutes(hhmm):
    """Convert a validated wall-clock time to minutes of its local day."""
    hours, minutes = map(int, hhmm.split(':'))
    return hours*60+minutes


def _intervals(config, local_day):
    """Build positive and blocked intervals, including yesterday's overnight windows."""
    intervals = {'available': [], 'unavailable': []}
    for day in (local_day-timedelta(days=1), local_day):
        for window in config.windows:
            if day.isoweekday() != window.weekday:
                continue
            start, end = _minutes(window.starts_local), _minutes(window.ends_local)
            finish_day = day + timedelta(days=int(end < start))
            low = resolve_local_minutes(day, start, config.timezone)
            high = resolve_local_minutes(finish_day, end, config.timezone)
            if low < high:
                intervals[window.kind].append((low, high))
    return intervals


def _free_until(now, available, blocked, day_end):
    """Find the end of the current contiguous window, subtracting calendar and hard exclusions."""
    boundary = now
    for start, end in sorted(available):
        if start <= boundary < end:
            boundary = end
    boundary = min(boundary, day_end)
    for start, end in blocked:
        if start <= now < end:
            return now
        if now < start < boundary:
            boundary = start
    return boundary


async def _records(db, user_id, day_start, day_end):
    """Load active account-scoped todos and calendar records relevant to this day's upper bound."""
    todos = (await db.execute(select(MatterTodo, Matter).join(Matter, Matter.matter_id==MatterTodo.matter_id).where(
        MatterTodo.user_id==user_id, Matter.user_id==user_id, Matter.status=='active',
        MatterTodo.deleted_at.is_(None), Matter.deleted_at.is_(None), MatterTodo.status=='open'
    ))).all()
    schedules = (await db.execute(select(ScheduleEvent, Matter).outerjoin(Matter, Matter.matter_id==ScheduleEvent.matter_id).where(
        ScheduleEvent.user_id==user_id, ScheduleEvent.deleted_at.is_(None), ScheduleEvent.status=='scheduled',
        ScheduleEvent.starts_at < day_end,
        or_(ScheduleEvent.ends_at > day_start,
            ScheduleEvent.ends_at.is_(None) & (ScheduleEvent.starts_at >= day_start)),
        or_(ScheduleEvent.matter_id.is_(None), (Matter.user_id==user_id) & Matter.deleted_at.is_(None)),
    ))).all()
    return todos, schedules


def _todo_action(row, matter, now, boundary, availability_status):
    """Rank a todo using only known time; lack of place/route evidence never implies it is reachable."""
    if row.available_after and ensure_aware(row.available_after)>now:
        return None
    due = ensure_aware(row.due_at) if row.due_at else None
    duration = row.estimated_duration_minutes
    limit = min(due, boundary) if due and due > now else boundary
    needed = timedelta(minutes=duration) if duration is not None else None
    unknown_travel = row.place is not None
    startable = boundary > now or availability_status == 'availability_missing'
    risk = due is not None and (due <= now or (needed is not None and due-now < needed))
    if not risk and not startable:
        return None
    if risk or (due and due < boundary + timedelta(minutes=30) and needed is not None
                and boundary > now and limit-now < needed):
        group, state = 1, 'deadline_risk'
        reason = '截止已到或剩余空档不足以容纳预计耗时，请先处理或调整截止。'
    elif row.due_schedule_offset_seconds is not None and not due:
        group, state, reason = 4, 'needs_deadline', '原日程已不可用，请重新确定截止时间。'
    elif duration is None:
        group, state, reason = 4, 'needs_duration', '缺少预计耗时，无法确认能否在空档完成。'
    elif availability_status != 'ready':
        group, state, reason = 4, 'needs_availability', '未确认可用空档，不能判断能否及时完成。'
    elif limit-now >= needed:
        group, state = 3, 'fits_window'
        reason = '当前空档可容纳预计处理耗时。'
    else:
        return None
    if unknown_travel:
        reason += '未核对当前位置或路程；上述判断不含出行。'
    return (group, PRIORITY.get(row.priority, 2) if group != 1 else 0, due or datetime.max.replace(tzinfo=UTC),
            ensure_aware(row.created_at), row.todo_id), {
        'kind':'todo','record_id':row.todo_id,'title':row.title,'matter_id':matter.matter_id,
        'matter_version':matter.version,'state':state,'reason':reason,'due_at':due,
        'window_end':limit if limit>now else None,'estimated_duration_minutes':duration,
        'actions':['view','complete','reschedule','snooze','dismiss'],
        'record_updated_at':ensure_aware(row.updated_at)}


async def _calculate(db, user, default_timezone, now, feedback):
    """Create comparable recommendation data and prune stale or expired feedback."""
    config = await read_availability(db, user, default_timezone)
    local_day = now.astimezone(ZoneInfo(config.timezone)).date()
    start, end = (resolve_local_minutes(day, 0, config.timezone)
                  for day in (local_day, local_day+timedelta(days=1)))
    todos, schedules = await _records(db, user.user_id, start, end)
    departures = await saved_departures(db,user,[row for row,_ in schedules],now)
    intervals = _intervals(config, local_day)
    busy = []
    uncertain = False
    ranked = []
    live = {}
    for row, matter in schedules:
        begins = ensure_aware(row.starts_at)
        ends = ensure_aware(row.ends_at) if row.ends_at else end
        busy.append((begins, ends))
        uncertain |= row.ends_at is None and begins <= now < ends
        departure = departures.get(row.schedule_id)
        depart_at = departure.departure_at if departure else None
        plan_id = departure.commute_plan_id if departure else None
        due = depart_at is not None and depart_at<=now
        if (begins < now and not due) or (matter and matter.status not in ('active','waiting')):
            continue
        route_status = 'available' if depart_at else 'unavailable' if row.location or row.latitude is not None or plan_id else None
        reason = ('已到保存路线的建议出发时间，实际路况可能变化。' if due else
                  '今天已有日程即将开始。' if begins-now<=timedelta(minutes=20) else '今天已有这个日程，可提前准备。')
        if route_status == 'unavailable':
            reason += '目前没有可用的已保存路线，无法确认出发时间。'
        item = {'kind':'schedule','record_id':row.schedule_id,'title':row.title,'matter_id':row.matter_id,
            'matter_version':matter.version if matter else None,'starts_at':begins,
            'state':'departure_due' if due else 'upcoming_schedule',
            'reason':reason,'destination':row.location or row.location_address,'route_status':route_status,
            'departure_at':depart_at,'commute_plan_id':plan_id,
            'actions':['view','snooze','dismiss'],'record_updated_at':ensure_aware(row.updated_at)}
        if matter and not plan_id and can_edit_schedule(row.source, row.data_source_id):
            item['actions'].append('reschedule')
        rank = -1 if due else 0 if begins-now<=timedelta(minutes=20) else 4
        ranked.append(((rank, 0, depart_at if due else begins, begins, row.schedule_id), item))
        live[f'schedule:{row.schedule_id}'] = item
    blocks = busy + intervals['unavailable']
    boundary = _free_until(now, intervals['available'], blocks, end)
    status = ('calendar_end_unknown' if uncertain else 'ready' if boundary>now else
              'no_current_window' if config.windows or any(a<=now<b for a,b in blocks) else 'availability_missing')
    for row, matter in todos:
        action = _todo_action(row, matter, now, boundary, status)
        if action:
            ranked.append(action)
        live[f'todo:{row.todo_id}'] = {'title':row.title,'due_at':ensure_aware(row.due_at) if row.due_at else None,
            'record_updated_at':ensure_aware(row.updated_at),'matter_version':matter.version}
    retained, suppressed = {}, []
    for key, entry in sorted(feedback.items()):
        if key not in live or datetime.fromisoformat(entry['until']) <= now:
            continue
        retained[key] = entry
        kind, record_id = key.split(':', 1)
        suppressed.append({**live[key], 'kind':kind,'record_id':record_id, **entry})
    visible = [item for _,item in sorted(ranked) if f"{item['kind']}:{item['record_id']}" not in retained][:3]
    return memory_applications.json_values({
        'timezone':config.timezone,'data_status':status,'availability_version':config.version,
        'primary':visible[0] if visible else None,'alternatives':visible[1:],'suppressed':suppressed,
    }), retained, end


async def _lock_state(db, user_id):
    """Serialize changing the snapshot and applying feedback in the same SQLite transaction."""
    await db.execute(insert(NextActionState).values(user_id=user_id,version=0,feedback={}).on_conflict_do_nothing())
    return await db.get(NextActionState, user_id)


async def query(db, user, default_timezone):
    """Refresh only owned recommendations and reuse equivalent unexpired snapshots."""
    state = await _lock_state(db, user.user_id)
    now = server_now()
    data, feedback, end = await _calculate(db, user, default_timezone, now, state.feedback)
    if not state.expires_at or ensure_aware(state.expires_at)<=now or data != state.snapshot:
        state.snapshot, state.feedback = data, feedback
        state.recommendation_id, state.version = new_id(), state.version+1
        state.calculated_at, state.expires_at = now, min(end, now+SNAPSHOT_TTL)
    result = NextActionRead(**data, recommendation_id=state.recommendation_id,version=state.version,
                            calculated_at=state.calculated_at,expires_at=state.expires_at)
    await db.commit()
    return result


async def command(db, user, payload, default_timezone):
    """Revalidate the snapshot and actual target before feeding back, with exact-key replay."""
    state = await _lock_state(db, user.user_id)
    request = payload.model_dump(mode='json', exclude={'idempotency_key'})
    prior = await db.get(NextActionCommandReceipt, (user.user_id, payload.idempotency_key))
    if prior:
        if request != prior.request:
            raise ConflictError('idempotency_key_reused','同一请求标识不能用于不同操作。')
        return prior.result
    if not state.recommendation_id:
        raise NotFoundError('recommendation_not_found','请先查询当前账号的推荐。')
    now = server_now()
    if (state.recommendation_id != payload.recommendation_id or state.version != payload.expected_recommendation_version
            or not state.expires_at or ensure_aware(state.expires_at)<=now):
        raise ConflictError('recommendation_changed','推荐已刷新或过期，请重新读取。')
    data, feedback, day_end = await _calculate(db,user,default_timezone,now,state.feedback)
    if data != state.snapshot:
        raise ConflictError('recommendation_changed','日程、待办或可用时间已变化，请重新读取推荐。')
    eligible = data['suppressed'] if payload.action=='resume' else [data['primary'], *data['alternatives']]
    if not any(item and item['kind']==payload.kind and item['record_id']==payload.record_id for item in eligible):
        raise NotFoundError('recommendation_target_not_found','该记录不属于本次可操作推荐。')
    key = f'{payload.kind}:{payload.record_id}'
    until = payload.until
    if payload.action == 'resume':
        feedback.pop(key, None)
    else:
        if payload.action == 'dismiss':
            until = day_end
        if not now < until <= now+timedelta(days=30):
            raise InvalidRequestError('snooze_time_invalid','暂缓时刻必须在未来30天内。')
        if len(feedback)>=100:
            raise ConflictError('recommendation_feedback_full','暂缓和忽略列表已满，请先恢复已有建议。')
        feedback[key] = {'state':'snoozed' if payload.action=='snooze' else 'dismissed','until':until.isoformat()}
    state.feedback, state.snapshot, state.expires_at = feedback, None, None
    result = NextActionReceipt(kind=payload.kind,record_id=payload.record_id,action=payload.action,
                               until=until).model_dump(mode='json')
    db.add(NextActionCommandReceipt(user_id=user.user_id, key=payload.idempotency_key, request=request, result=result))
    await db.commit()
    return result
