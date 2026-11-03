"""Lifecycle operations for linked commute records; all writes remain account scoped."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from assistant_api.models import CommutePlan, MatterDraft, ScheduleEvent
from assistant_api.core.errors import ConflictError, InvalidRequestError
from assistant_api.services import commute_estimate, schedule_routes
from assistant_api.commute.services.plans import _cancel_stale_work

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

async def revision_draft(db, runtime, settings, user, run, parsed, text, *, origin_key=None, allow_create=False):
    """Prepare a server-measured day edit for both the legacy planner and public business command."""
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
    basis = schedule or await db.get(ScheduleEvent, plan.first_schedule_id)
    if not basis or basis.user_id != user.user_id:
        raise ConflictError('commute_basis_missing', '通勤计划缺少原地点依据。')
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
    route = await commute_estimate.estimate(runtime, origin=(plan.origin_latitude,plan.origin_longitude),
        destination=(plan.destination_latitude,plan.destination_longitude), mode=plan.transport_mode,
        depart_at=arrival-timedelta(hours=1), arrive_by=arrival,
        origin_city=basis.location_city, destination_city=basis.location_city,
        origin_coordinate_system='gcj02',destination_coordinate_system='gcj02',
        schedule_id=schedule.schedule_id if schedule else None)
    if route['status'] != 'available':
        draft.payload_json={**draft.payload_json,'commute_revision':{**draft.payload_json['commute_revision'],'status':'failed'}}
        await db.commit()
        raise ConflictError('commute_route_unavailable', '这次未取得通勤测算结果，请稍后再试。')
    end=arrival-timedelta(minutes=plan.buffer_minutes);start=end-timedelta(seconds=route['duration_seconds'])
    proposed={k:v for k,v in matters._serialize_schedule(basis).items() if k in ['title','location','latitude','longitude','coordinate_system','location_city','location_address','location_source','location_external_id']}
    if schedule: proposed['schedule_id']=schedule.schedule_id
    else: proposed['title']=plan.name
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
    instance=await ensure_instance(db,plan,now=now,service_date=service_date)
    if not instance:raise ConflictError('commute_date_ineligible','已错过目标到达时间。')
    claim=await db.execute(update(CommuteInstance).where(CommuteInstance.instance_id==instance.instance_id,CommuteInstance.status=='active').values(status='materializing'))
    if claim.rowcount!=1:raise ConflictError('commute_generation_pending','已有生成任务或未知结果，请先核查原实例。')
    await db.commit()
    arrival=instance.target_arrival_at.replace(tzinfo=UTC) if instance.target_arrival_at.tzinfo is None else instance.target_arrival_at
    route=await commute_estimate.estimate(runtime,origin=(plan.origin_latitude,plan.origin_longitude),destination=(plan.destination_latitude,plan.destination_longitude),mode=plan.transport_mode,depart_at=arrival-timedelta(hours=1),arrive_by=arrival,origin_city=first.location_city,destination_city=first.location_city,origin_coordinate_system='gcj02',destination_coordinate_system='gcj02')
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
        location=first.location,latitude=plan.destination_latitude,longitude=plan.destination_longitude,coordinate_system='gcj02',location_city=first.location_city,location_address=first.location_address,location_source=first.location_source)
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
