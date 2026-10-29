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

async def revision_draft(db, runtime, settings, user, run, parsed, text):
    from assistant_api.services import matters
    target = await matters._get_matter(db, user, parsed['matter_id'])
    plans = await active_plans(db, user, target.matter_id)
    if len(plans) != 1:
        raise InvalidRequestError('commute_plan_ambiguous', '该事项没有唯一有效通勤计划，请先明确计划。')
    plan = plans[0]
    request = parsed['commute_update']
    day = date.fromisoformat(request['service_date'])
    arrival = datetime.fromisoformat(f"{day}T{request['arrival_local_time']}:00").replace(tzinfo=ZoneInfo(plan.timezone))
    if arrival <= datetime.now(UTC):
        raise InvalidRequestError('arrival_passed', '目标到达时间已过去。')
    rows = list(await db.scalars(select(ScheduleEvent).where(
        ScheduleEvent.user_id == user.user_id, ScheduleEvent.matter_id == target.matter_id,
        ScheduleEvent.deleted_at.is_(None), ScheduleEvent.status == 'scheduled')))
    matches = [r for r in rows if matters._as_utc(r.starts_at).astimezone(ZoneInfo(plan.timezone)).date() == day]
    if len(matches) != 1:
        raise InvalidRequestError('commute_schedule_ambiguous', '目标日期没有唯一通勤日程，请先明确安排。')
    schedule = matches[0]
    draft = MatterDraft(user_id=user.user_id, matter_id=target.matter_id, ai_run_id=run.local_run_id,
        kind='update', status='preview', version=1, base_matter_version=target.version,
        base_schedule_id=schedule.schedule_id, base_schedule_updated_at=schedule.updated_at,
        payload_json={'kind':'update','matter_id':target.matter_id,'title':target.title,
            'goal':target.goal,'source_text':text,'changes_note':text,'schedule':None,
            'todos':parsed['todos'],'complete_todo_ids':[], 'matter_action':'none',
            'commute_revision':{'status':'measuring','plan_id':plan.plan_id,'plan_revision':plan.revision}})
    draft.payload_json = schedule_routes._jsonable(draft.payload_json)
    db.add(draft); await db.commit()
    route = await commute_estimate.estimate(runtime, origin=(plan.origin_latitude,plan.origin_longitude),
        destination=(plan.destination_latitude,plan.destination_longitude), mode=plan.transport_mode,
        depart_at=arrival-timedelta(hours=1), arrive_by=arrival,
        origin_city=schedule.location_city, destination_city=schedule.location_city,
        origin_coordinate_system='gcj02',destination_coordinate_system='gcj02', schedule_id=schedule.schedule_id)
    if route['status'] != 'available':
        draft.payload_json={**draft.payload_json,'commute_revision':{**draft.payload_json['commute_revision'],'status':'failed','route':schedule_routes._jsonable(route)}}
        await db.commit(); return draft
    end=arrival-timedelta(minutes=plan.buffer_minutes);start=end-timedelta(seconds=route['duration_seconds'])
    proposed={k:v for k,v in matters._serialize_schedule(schedule).items() if k in ['schedule_id','title','location','latitude','longitude','coordinate_system','location_city','location_address','location_source','location_external_id']}
    proposed.update(starts_at=start.isoformat(),ends_at=end.isoformat())
    revision={**draft.payload_json['commute_revision'],'status':'ready','route':schedule_routes._jsonable(route),
        'schedule':proposed,'target_arrival_at':arrival.isoformat(),'buffer_minutes':plan.buffer_minutes,
        'expires_at':(datetime.now(UTC)+timedelta(minutes=30)).isoformat()}
    payload={**draft.payload_json,'schedule':proposed,'commute_revision':revision}
    payload['action_plan']=matters._build_action_plan(payload)
    draft.payload_json=schedule_routes._jsonable(payload)
    draft.conflict_json=schedule_routes._jsonable(await matters._conflict_for_payload(db,runtime,user,payload,settings=settings,timezone=plan.timezone,exclude_schedule_id=schedule.schedule_id))
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
    """Materialize one explicitly targeted day, using the existing unique instance."""
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
    arrival=datetime.fromisoformat(f'{service_date}T{plan.arrival_local_time}:00').replace(tzinfo=zone)
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
