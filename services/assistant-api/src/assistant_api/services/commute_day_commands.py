"""Complete owned commute-day commands using the existing measured draft save.

Formal schedule, route, override and dependent reminders share confirm_draft's
transaction. Provider calls prepare a preview; no formal partial write is made.
"""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import or_, select
from assistant_api.models import CommuteInstance, CommutePlan, MatterDraft, ScheduleEvent
from assistant_api.core.errors import ConflictError
from assistant_api.commute.services.plans import get_plan
from assistant_api.commute.services.scheduling import get_instance, cancel_pending_checks
from assistant_api.services import matters, schedule_routes
from assistant_api.schemas.matters import ConfirmRequest


async def day_schedule(db, user, plan, service_date):
    """Find the plan's exact first or generated daily record, never a same-title meeting."""
    key=f'commute:{plan.plan_id}:{service_date}'
    rows=list(await db.scalars(select(ScheduleEvent).where(
        ScheduleEvent.user_id==user.user_id, ScheduleEvent.matter_id==plan.matter_id,
        ScheduleEvent.deleted_at.is_(None), ScheduleEvent.status=='scheduled',
        or_(ScheduleEvent.schedule_id==plan.first_schedule_id, ScheduleEvent.source_external_id==key))))
    matches=[row for row in rows if matters._as_utc(row.starts_at).astimezone(ZoneInfo(plan.timezone)).date()==service_date]
    if len(matches)>1:
        raise ConflictError('commute_schedule_ambiguous','这一天有多条通勤安排，请明确目标。')
    return matches[0] if matches else None


async def read_day(db, runtime, user, plan_id, service_date):
    """Expose the effective day, actual state and versions so AI can ask before editing expired time."""
    plan=await get_plan(db,user.user_id,plan_id)
    matter=await matters._get_matter(db,user,plan.matter_id) if plan.matter_id else None
    instance=await get_instance(db,plan_id=plan_id,local_service_date=service_date)
    clock=instance.arrival_local_time_override if instance and instance.arrival_local_time_override else plan.arrival_local_time
    target=datetime.combine(service_date,datetime.strptime(clock,'%H:%M').time(),ZoneInfo(plan.timezone))
    eligible=service_date.isoweekday() in plan.weekdays_json and (not plan.effective_from or service_date>=plan.effective_from)
    status='inactive' if not plan.enabled or not matter or matter.status not in ('active','waiting') else 'ineligible' if not eligible else 'past' if target<=matters._as_utc(runtime.now()) else 'upcoming'
    schedule=await day_schedule(db,user,plan,service_date) if matter else None
    route=await schedule_routes.valid_for_schedule(db,user,schedule) if schedule else None
    if route and route.get('target_arrival_at') and matters._as_utc(datetime.fromisoformat(route['target_arrival_at'].replace('Z','+00:00')))!=target.astimezone(UTC):
        route=None
    return {'plan_id':plan_id,'matter_id':plan.matter_id,'version':matter.version if matter else None,
            'revision':plan.revision,'service_date':service_date,'timezone':plan.timezone,
            'status':status,'target_arrival_at':target,'arrival_local_time':clock,
            'schedule':matters._serialize_schedule(schedule) if schedule else None,'route':route}


async def change_day(db, runtime, settings, user, plan_id, service_date, request, *, refresh=False):
    """Remeasure and atomically save the same day, or replay its original completed command."""
    from assistant_api.services import commute_lifecycle
    signature={'plan_id':plan_id,'service_date':str(service_date),'refresh':refresh,
               'request':request.model_dump(mode='json',exclude={'service_date'})}
    existing=await db.scalar(select(MatterDraft).where(MatterDraft.user_id==user.user_id,MatterDraft.origin_key==request.idempotency_key))
    if existing:
        if (existing.payload_json or {}).get('analysis',{}).get('day_request')!=signature:
            raise ConflictError('idempotency_key_reused','该请求标识已用于其他修改。')
        if existing.status!='confirmed':
            raise ConflictError('commute_update_pending','这次测算尚未完成，请查询原请求。')
        result=await matters.confirm_draft(db,runtime,settings,user,existing.draft_id,
            ConfirmRequest(expected_version=existing.version),request.idempotency_key)
        return {**await read_day(db,runtime,user,plan_id,service_date),'applied':True,'replayed':True,'change_id':result['change_id']}
    state=await read_day(db,runtime,user,plan_id,service_date)
    if state['status'] in ('inactive','ineligible'):
        raise ConflictError('commute_inactive','该日期没有有效的通勤计划。')
    arrival=state['arrival_local_time'] if refresh else request.arrival_local_time
    target=datetime.combine(service_date,datetime.strptime(arrival,'%H:%M').time(),ZoneInfo(state['timezone']))
    if target<=matters._as_utc(runtime.now()):
        raise ConflictError('arrival_passed','这个到达时间已过，请指定日期和时间。')
    if not refresh and (state['revision']!=request.expected_plan_revision or state['version']!=request.expected_matter_version):
        raise ConflictError('commute_version_changed','安排已更新，请重新读取。')
    parsed={'matter_id':state['matter_id'],'todos':[],'commute_update':{
        'plan_id':plan_id,'service_date':str(service_date),'arrival_local_time':arrival,'set_override':not refresh}}
    draft=await commute_lifecycle.revision_draft(db,runtime,settings,user,None,parsed,
        f'{service_date}通勤'+('更新' if refresh else '到达时间修改'),origin_key=request.idempotency_key,allow_create=True)
    draft.payload_json={**draft.payload_json,'analysis':{'day_request':signature}}
    await db.commit()
    result=await matters.confirm_draft(db,runtime,settings,user,draft.draft_id,
        ConfirmRequest(expected_version=draft.version),request.idempotency_key)
    if not result.get('applied',True):
        return {**state,'applied':False,'replayed':False,'conflict':result.get('conflict')}
    return {**await read_day(db,runtime,user,plan_id,service_date),'applied':True,'replayed':False,'change_id':result['change_id']}


async def require_measured_day_edit(db, user, payload):
    """Keep route-owned time/location edits in a measured business operation for both Web and AI."""
    proposed=payload.get('schedule') or {}
    if not proposed.get('schedule_id') or payload.get('commute_revision'):
        return
    schedule=await db.get(ScheduleEvent,proposed['schedule_id'])
    if not schedule or schedule.user_id!=user.user_id:
        return
    plan=await db.scalar(select(CommutePlan).where(CommutePlan.user_id==user.user_id,
        or_(CommutePlan.first_schedule_id==schedule.schedule_id,
            CommutePlan.plan_id==str(schedule.source_scope_key or '').removeprefix('commute:'))))
    if not plan:
        return
    for key in ('starts_at','ends_at','location','latitude','longitude','coordinate_system','location_city','location_address'):
        old=getattr(schedule,key);new=proposed.get(key)
        if key.endswith('_at'):
            old=matters._as_utc(old) if old else None
            new=matters._as_utc(datetime.fromisoformat(new.replace('Z','+00:00'))) if new else None
        if old!=new:
            raise ConflictError('commute_day_update_required','请使用通勤当天修改接口，系统会同时更新出发时间和路线。')


async def apply_day_revision(db, user, payload, schedule):
    """Save a measured override and invalidate old pending work inside the formal transaction."""
    revision=payload['commute_revision']
    plan=await get_plan(db,user.user_id,revision['plan_id'])
    target=datetime.fromisoformat(revision['target_arrival_at']).astimezone(UTC)
    # Older persisted quotes already carry the exact target instant; derive
    # the day fields from that fact instead of invalidating their saved format.
    local_target=target.astimezone(ZoneInfo(plan.timezone))
    day=date.fromisoformat(revision['service_date']) if revision.get('service_date') else local_target.date()
    instance=await get_instance(db,plan_id=plan.plan_id,local_service_date=day)
    if instance is None:
        instance=CommuteInstance(user_id=user.user_id,plan_id=plan.plan_id,plan_revision=plan.revision,
            local_service_date=day,timezone=plan.timezone,target_arrival_at=target,
            check_window_start_at=datetime.combine(day,datetime.strptime(plan.check_local_time,'%H:%M').time(),ZoneInfo(plan.timezone)),status='active')
        db.add(instance);await db.flush()
    changed=matters._as_utc(instance.target_arrival_at)!=target
    if revision.get('set_override',True):
        instance.arrival_local_time_override=revision.get('arrival_local_time') or local_target.strftime('%H:%M')
    instance.target_arrival_at=target
    if instance.status in ('expired','closed'):
        instance.status='active'
    if changed:
        await cancel_pending_checks(db,instance_id=instance.instance_id,code='commute_day_changed',now=datetime.now(UTC))
    if revision.get('new_day'):
        schedule.source_external_id=f'commute:{plan.plan_id}:{day}'
        schedule.source_scope_key=f'commute:{plan.plan_id}'
    await db.flush()


async def remeasure_next_day(db, runtime, settings, user, plan):
    """Refresh the next eligible future day immediately after a recurring configuration save."""
    from datetime import timedelta
    if not plan.enabled or not plan.matter_id or not plan.first_schedule_id:
        return None
    now=matters._as_utc(runtime.now());zone=ZoneInfo(plan.timezone)
    first=max(now.astimezone(zone).date(),plan.effective_from or now.astimezone(zone).date())
    for offset in range(8):
        day=first+timedelta(days=offset)
        state=await read_day(db,runtime,user,plan.plan_id,day)
        if state['status']!='upcoming':
            continue
        from assistant_api.commute.schemas import CommuteDayRefresh
        request=CommuteDayRefresh(service_date=day,idempotency_key=f'plan-update:{plan.plan_id}:v{plan.revision}')
        try:
            return await change_day(db,runtime,settings,user,plan.plan_id,day,request,refresh=True)
        except ConflictError as exc:
            if exc.code not in ('commute_route_unavailable','conflict_detected'):
                raise
            # Recurring settings are already saved. Keep the actual day state;
            # the durable evening/morning follow-up will request a fresh route.
            return {**await read_day(db,runtime,user,plan.plan_id,day),'applied':False}
    return None
