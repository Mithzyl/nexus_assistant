"""Lifecycle operations for linked commute records; all writes remain account scoped."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from pydantic import ValidationError
from sqlalchemy import select
from assistant_api.models import CommutePlan, MatterDraft, ScheduleEvent
from assistant_api.core.errors import AssistantError, ConflictError, InvalidRequestError
from assistant_api.services import commute_estimate, place_queries, schedule_routes
from assistant_api.commute.schemas import CommuteLocation
from assistant_api.commute.providers.base import ProviderError
from assistant_api.commute.serializers import saved_plan_location
from assistant_api.commute.services.plans import _cancel_stale_work, route_locations

COMMUTE_LOCATION_INCOMPLETE = 'commute_location_incomplete'

async def active_plans(db, user, matter_id):
    return list(await db.scalars(select(CommutePlan).where(
        CommutePlan.user_id == user.user_id, CommutePlan.matter_id == matter_id,
        CommutePlan.enabled.is_(True))))

async def stop_plans(db, user, matter_id):
    ids = []
    for plan in await active_plans(db, user, matter_id):
        plan.enabled = False
        plan.revision += 1
        await _cancel_stale_work(db, plan, now=datetime.now(UTC))
        ids.append(plan.plan_id)
    return ids

def plan_location(plan: CommutePlan, endpoint: str) -> CommuteLocation:
    """Validate persisted coordinates and labels, including rows created before current input validation."""
    try:
        return saved_plan_location(plan, endpoint)
    except ValidationError as exc:
        label = '起点' if endpoint == 'origin' else '终点'
        raise AssistantError(
            COMMUTE_LOCATION_INCOMPLETE,
            f'通勤计划的{label}名称或坐标缺失或无效。请核对计划地点，本次未写入日程或路线。',
            422,
            {'fields': [f'{endpoint}.{issue["loc"][0]}' for issue in exc.errors(include_input=False)],
             'formal_write_status': 'not_applied'},
        ) from exc


async def transit_city(runtime, point: CommuteLocation, endpoint: str) -> str:
    """Resolve the actual transit city from saved coordinates before creating a draft."""
    result = await place_queries.map_pick_place(runtime, latitude=point.latitude, longitude=point.longitude,
                                              coordinate_system='gcj02')
    city = (result.get('location') or {}).get('city')
    if city and city.strip():
        return city.strip()
    label = '起点' if endpoint == 'origin' else '终点'
    raise AssistantError(
        COMMUTE_LOCATION_INCOMPLETE,
        f'无法确认通勤计划{label}的公交城市。请核对计划地点后再测算，本次未写入日程或路线。',
        422,
        {'fields': [f'{endpoint}.city'], 'formal_write_status': 'not_applied',
         'reason_code': result.get('reason_code') or 'transit_city_required'},
    )


async def revision_draft(db, runtime, settings, user, run, parsed, text, *, origin_key=None,
                         allow_create=False, weather_buffer_seconds=0):
    """Measure the saved plan and use a matching calendar record only for optional place metadata."""
    from assistant_api.services import matters
    from assistant_api.commute.services.plans import get_plan
    from assistant_api.services.commute_day_commands import day_schedule
    target = await matters._get_matter(db, user, parsed['matter_id'])
    request = parsed['commute_update']
    if request.get('plan_id'):
        plan = await get_plan(db, user.user_id, request['plan_id'])
        if plan.matter_id != target.matter_id or not plan.enabled:
            raise ConflictError('commute_inactive', '该通勤计划已停止。')
    else:
        plans = await active_plans(db, user, target.matter_id)
        if len(plans) != 1:
            raise InvalidRequestError('commute_plan_ambiguous', '请明确要修改的通勤计划。')
        plan = plans[0]
    day = date.fromisoformat(request['service_date'])
    now = matters._as_utc(runtime.now())
    arrival = datetime.fromisoformat(f"{day}T{request['arrival_local_time']}:00").replace(tzinfo=ZoneInfo(plan.timezone))
    if arrival <= now:
        raise ConflictError('arrival_passed', '这个到达时间已过，请指定要修改的日期和时间。')
    if (plan.effective_from and day < plan.effective_from) or day.isoweekday() not in plan.weekdays_json:
        raise ConflictError('commute_date_ineligible', '该日期不在通勤计划的生效范围内。')
    schedule = await day_schedule(db, user, plan, day)
    if schedule is None and not allow_create:
        raise InvalidRequestError('commute_schedule_ambiguous', '目标日期没有通勤日程，请先读取当天安排。')
    origin = plan_location(plan, 'origin')
    destination = plan_location(plan, 'destination')
    try:
        route_origin, route_destination = await route_locations(runtime, origin, destination)
    except ProviderError as exc:
        raise ConflictError('commute_route_unavailable', '本次未取得有效的路线坐标，尚未更新日程。') from exc
    basis = schedule
    if basis is None and plan.first_schedule_id:
        basis = await db.get(ScheduleEvent, plan.first_schedule_id)
    proposed = {
        'title': schedule.title if schedule else plan.name,
        'location': destination.label, 'latitude': destination.latitude, 'longitude': destination.longitude,
        'coordinate_system': destination.coordinate_system or 'gcj02',
        'location_city': destination.city,
    }
    if (basis and basis.user_id == user.user_id and basis.matter_id == plan.matter_id
            and basis.deleted_at is None and basis.coordinate_system == proposed['coordinate_system']
            and (basis.latitude, basis.longitude) == (destination.latitude, destination.longitude)):
        for field in ('location_address', 'location_source', 'location_external_id'):
            proposed[field] = getattr(basis, field)
        proposed['location_city'] = destination.city or basis.location_city
    origin_city = origin.city
    destination_city = proposed.get('location_city')
    if plan.transport_mode == 'transit' and runtime.eta.provider == 'amap':
        origin_city = origin_city or await transit_city(runtime, route_origin, 'origin')
        if not destination_city or not destination_city.strip():
            destination_city = await transit_city(runtime, route_destination, 'destination')
            proposed['location_city'] = destination_city
    draft = MatterDraft(user_id=user.user_id, matter_id=target.matter_id,
        ai_run_id=run.local_run_id if run else None, origin_key=origin_key,
        kind='update', status='preview', version=1, base_matter_version=target.version,
        base_schedule_id=schedule.schedule_id if schedule else None,
        base_schedule_updated_at=schedule.updated_at if schedule else None,
        payload_json={'kind':'update','matter_id':target.matter_id,'title':target.title,
            'goal':target.goal,'source_text':text,'changes_note':text,'schedule':None,
            'todos':parsed.get('todos',[]),'complete_todo_ids':[], 'matter_action':'none',
            'commute_revision':{'status':'measuring','plan_id':plan.plan_id,'plan_revision':plan.revision,
                'service_date':str(day),'arrival_local_time':request['arrival_local_time'],
                'set_override':request.get('set_override',True),'new_day':schedule is None}})
    draft.payload_json = schedule_routes._jsonable(draft.payload_json)
    db.add(draft); await db.commit()
    route = await commute_estimate.estimate(runtime, origin=(route_origin.latitude,route_origin.longitude),
        destination=(route_destination.latitude,route_destination.longitude), mode=plan.transport_mode,
        depart_at=arrival-timedelta(hours=1), arrive_by=arrival,
        origin_city=origin_city, destination_city=destination_city,
        origin_coordinate_system=route_origin.coordinate_system or 'gcj02',
        destination_coordinate_system=route_destination.coordinate_system or 'gcj02',
        schedule_id=schedule.schedule_id if schedule else None)
    if route['status'] != 'available':
        draft.payload_json={**draft.payload_json,'commute_revision':{**draft.payload_json['commute_revision'],'status':'failed'}}
        await db.commit()
        raise ConflictError('commute_route_unavailable', '这次未取得通勤测算结果，请稍后再试。')
    end = arrival - timedelta(minutes=plan.buffer_minutes, seconds=weather_buffer_seconds)
    start = end - timedelta(seconds=route["duration_seconds"])
    if schedule: proposed['schedule_id']=schedule.schedule_id
    proposed.update(starts_at=start.isoformat(),ends_at=end.isoformat())
    revision={**draft.payload_json['commute_revision'],'status':'ready','route':schedule_routes._jsonable(route),
        'schedule':proposed,'target_arrival_at':arrival.isoformat(),'buffer_minutes':plan.buffer_minutes,
        'expires_at':(datetime.now(UTC)+timedelta(minutes=30)).isoformat()}
    payload={**draft.payload_json,'schedule':proposed,'commute_revision':revision}
    payload['action_plan']=matters._build_action_plan(payload)
    draft.payload_json=schedule_routes._jsonable(payload)
    draft.conflict_json=schedule_routes._jsonable(await matters._conflict_for_payload(db,runtime,user,payload,settings=settings,timezone=plan.timezone,exclude_schedule_id=schedule.schedule_id if schedule else None))
    await db.commit();return draft

async def validate_revision(db,user,payload):
    rev=payload.get('commute_revision')
    if not rev:return
    plan=await db.get(CommutePlan,rev['plan_id'])
    if not plan or plan.user_id!=user.user_id or not plan.enabled or plan.revision!=rev['plan_revision']:
        raise ConflictError('commute_plan_changed','通勤计划已变化，请重新整理。')
    if rev['status']!='ready' or datetime.fromisoformat(rev['expires_at'])<datetime.now(UTC):
        raise ConflictError('commute_route_unverified','本次路线尚未核实或已过期。')
    for key in ['starts_at','ends_at','latitude','longitude','location','coordinate_system']:
        a=(payload.get('schedule') or {}).get(key);b=rev['schedule'].get(key)
        if key.endswith('_at') and a and b:
            a=datetime.fromisoformat(a.replace('Z','+00:00'));b=datetime.fromisoformat(b.replace('Z','+00:00'))
        if a!=b:raise ConflictError('route_quote_mismatch','日程条件与核实路线不一致。')

async def generate_day(db, runtime, user, plan_id, service_date):
    """Materialize one targeted day with its effective override, reusing the unique daily instance."""
    from sqlalchemy import update
    from assistant_api.models import Matter, CommuteInstance
    from assistant_api.commute.services.plans import get_plan
    from assistant_api.commute.services.scheduling import ensure_instance
    plan = await get_plan(db,user.user_id,plan_id)
    matter = await db.get(Matter,plan.matter_id) if plan.matter_id else None
    if not plan.enabled or not matter or matter.user_id!=user.user_id or matter.status not in ['active','waiting'] or matter.deleted_at:
        raise ConflictError('commute_inactive','通勤计划或关联事项已停止，不能生成。')
    zone=ZoneInfo(plan.timezone);now=datetime.now(UTC)
    if service_date < now.astimezone(zone).date() or (plan.effective_from and service_date < plan.effective_from) or service_date.isoweekday() not in plan.weekdays_json:
        raise InvalidRequestError('commute_date_ineligible','该日期不在计划生效范围内。')
    source_key=f'commute:{plan_id}:{service_date}'
    existing=await db.scalar(select(ScheduleEvent).where(ScheduleEvent.user_id==user.user_id,ScheduleEvent.source_external_id==source_key))
    first=await db.get(ScheduleEvent,plan.first_schedule_id) if plan.first_schedule_id else None
    if not existing and first and first.starts_at.replace(tzinfo=UTC).astimezone(zone).date()==service_date:existing=first
    if existing:
        # Recover the pre-release SQLite offset-loss bug only when the corrected
        # values exactly match the original server-verified route fingerprint.
        from assistant_api.models import ScheduleRouteResult
        saved = await db.scalar(select(ScheduleRouteResult).where(
            ScheduleRouteResult.user_id == user.user_id,
            ScheduleRouteResult.schedule_id == existing.schedule_id))
        repaired = False
        if (existing.source_external_id == source_key and existing.status == 'scheduled'
                and not existing.deleted_at and saved
                and saved.schedule_fingerprint != schedule_routes.schedule_fingerprint(existing)):
            old_start, old_end = existing.starts_at, existing.ends_at
            existing.starts_at = old_start.replace(tzinfo=zone).astimezone(UTC)
            existing.ends_at = old_end.replace(tzinfo=zone).astimezone(UTC) if old_end else None
            if saved.schedule_fingerprint == schedule_routes.schedule_fingerprint(existing):
                repaired = True
                await db.commit()
            else:
                existing.starts_at, existing.ends_at = old_start, old_end
        return {'schedule_id':existing.schedule_id,'replayed':True,
                'timezone_repaired':repaired,'service_date':str(service_date)}
    if not first:raise ConflictError('commute_basis_missing','缺少已确认首日的地点依据。')
    original_origin, original_destination = plan_location(plan, 'origin'), plan_location(plan, 'destination')
    try:
        origin, destination = await route_locations(runtime, original_origin, original_destination)
    except ProviderError as exc:
        raise ConflictError('commute_route_unavailable','本次未取得有效的路线坐标，未生成日程。') from exc
    instance=await ensure_instance(db,plan,now=now,service_date=service_date)
    if not instance:raise ConflictError('commute_date_ineligible','已错过目标到达时间。')
    claim=await db.execute(update(CommuteInstance).where(CommuteInstance.instance_id==instance.instance_id,CommuteInstance.status=='active').values(status='materializing'))
    if claim.rowcount!=1:raise ConflictError('commute_generation_pending','已有生成任务或未知结果，请先核查原实例。')
    await db.commit()
    arrival=instance.target_arrival_at.replace(tzinfo=UTC) if instance.target_arrival_at.tzinfo is None else instance.target_arrival_at
    route=await commute_estimate.estimate(runtime,origin=(origin.latitude,origin.longitude),
        destination=(destination.latitude,destination.longitude),mode=plan.transport_mode,
        depart_at=arrival-timedelta(hours=1),arrive_by=arrival,
        origin_city=origin.city or first.location_city,destination_city=destination.city or first.location_city,
        origin_coordinate_system=origin.coordinate_system or 'gcj02',
        destination_coordinate_system=destination.coordinate_system or 'gcj02')
    # Recheck lifecycle after the upstream call before creating any formal record.
    await db.refresh(plan);await db.refresh(matter)
    if not plan.enabled or matter.status not in ['active','waiting']:
        instance.status='closed';await db.commit();raise ConflictError('commute_inactive','测算期间事项已停止，未生成日程。')
    if route['status']!='available':
        instance.status='generation_failed';await db.commit();raise ConflictError('commute_route_unavailable','真实路线不可用，未生成日程；请核查原实例。')
    end=arrival-timedelta(minutes=plan.buffer_minutes)
    row=ScheduleEvent(user_id=user.user_id,matter_id=matter.matter_id,title=plan.name,
        starts_at=(end-timedelta(seconds=route['duration_seconds'])).astimezone(UTC),ends_at=end.astimezone(UTC),status='scheduled',source='web_native',sync_status='local_only',
        source_external_id=source_key,source_scope_key=f'commute:{plan_id}',
        location=original_destination.label,latitude=original_destination.latitude,longitude=original_destination.longitude,
        coordinate_system=original_destination.coordinate_system or 'gcj02',
        location_city=original_destination.city or first.location_city,
        location_address=first.location_address if (first.latitude, first.longitude) == (original_destination.latitude, original_destination.longitude) else None,
        location_source=first.location_source)
    db.add(row);await db.flush();route['schedule_id']=row.schedule_id
    route['destination']={**route['destination'],'schedule_id':row.schedule_id,'basis':'destination_schedule'}
    await schedule_routes.save_for_schedule(db,user,schedule_id=row.schedule_id,result=route,commit=False)
    instance.status='active';await db.commit()
    return {'schedule_id':row.schedule_id,'instance_id':instance.instance_id,'service_date':str(service_date),'replayed':False,'route':schedule_routes._jsonable(route)}


async def materialize_follow_up_rules(app, *, now):
    """Publish evening-22:00 and morning refreshes through existing durable follow-up scheduling."""
    from assistant_api.services.follow_ups import aware, create_rule
    from assistant_api.services.api_operations import OperationCatalog
    from assistant_api.models import FollowUp, User, Matter
    from assistant_api.schemas.follow_ups import FollowUpCreate
    if not app.state.settings.commute_auto_ai_enabled:
        return 0
    catalog=OperationCatalog(app)
    read=next(op for op in catalog.operations.values() if op.operation_id=='get_commute_day')
    created=0
    async with app.state.session_factory() as db:
        plans=list(await db.scalars(select(CommutePlan).where(CommutePlan.enabled.is_(True))))
        for plan in plans:
            zone=ZoneInfo(plan.timezone);local=aware(now).astimezone(zone)
            matter=await db.get(Matter,plan.matter_id) if plan.matter_id else None
            if matter is None:
                matter=Matter(user_id=plan.user_id,title=plan.name,goal='按已保存的通勤计划安排出行')
                db.add(matter);await db.flush();plan.matter_id=matter.matter_id;await db.commit()
            if matter.deleted_at or matter.status not in ('active','waiting'):
                continue
            for day in (local.date(),local.date()+timedelta(days=1)):
                if day.isoweekday() not in plan.weekdays_json or (plan.effective_from and day<plan.effective_from):
                    continue
                from assistant_api.commute.services.scheduling import get_instance
                instance=await get_instance(db,plan_id=plan.plan_id,local_service_date=day)
                clock=instance.arrival_local_time_override if instance and instance.arrival_local_time_override else plan.arrival_local_time
                arrival=datetime.combine(day,datetime.strptime(clock,'%H:%M').time(),zone)
                if arrival<=local: continue
                phases=[('morning',datetime.combine(day,datetime.strptime(plan.check_local_time,'%H:%M').time(),zone))]
                if local.date()<day:
                    phases.append(('evening',datetime.combine(day-timedelta(days=1),datetime.strptime('22:00','%H:%M').time(),zone)))
                for phase,at in phases:
                    key=f'commute:{plan.plan_id}:{day.isoformat()}:'+('evening:' if phase=='evening' else '')+f'v{plan.revision}'
                    if await db.scalar(select(FollowUp.follow_up_id).where(FollowUp.user_id==plan.user_id,FollowUp.idempotency_key==key)):
                        continue
                    instruction=f'更新{day.isoformat()}的通勤出行安排。先读取该日期get_commute_day，若status=past或inactive则安静结束，不改历史。否则调用refresh_commute_day立即重测并保存同一天安排，返回真实出发时间和结果卡片。只更新所指日期，不修改长期配置。计划ID：{plan.plan_id}。'
                    payload=FollowUpCreate.model_validate({'idempotency_key':key,'matter_id':matter.matter_id,'title':plan.name+('次日出行安排' if phase=='evening' else '出行更新'),'instruction':instruction,'execution_mode':'execute','references':[{'name':'commute_day','operation_id':read.operation_id,'path_parameters':{'plan_id':plan.plan_id},'body':{'service_date':str(day)}}],'trigger':{'kind':'time','at':at.astimezone(UTC).isoformat()},'stop_when':[{'reference':'commute_day','field':'/revision','operator':'ne','value':plan.revision},{'reference':'commute_day','field':'/status','value':'past'},{'reference':'commute_day','field':'/status','value':'inactive'}]})
                    await create_rule(app,db,await db.get(User,plan.user_id),payload)
                    created+=1
    return created
