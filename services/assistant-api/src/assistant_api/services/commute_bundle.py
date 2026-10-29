"""Server-owned commute quotes and atomic business confirmation.

Quotes live in the existing account-owned draft, never in caller supplied ETA
fields. Provider failures remain explicit; no fallback route is manufactured.
"""
from __future__ import annotations
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy import update
from assistant_api.models import MatterDraft, CommutePlan
from assistant_api.models.base import new_id
from assistant_api.core.errors import ConflictError, InvalidRequestError
from assistant_api.services import commute_places, commute_estimate, schedule_routes

class CommuteIntent(BaseModel):
    origin_query: str = Field(min_length=1, max_length=255)
    destination_query: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=64)
    arrival_local_time: str = Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    buffer_minutes: int | None = Field(default=None, ge=0, le=120)
    effective_from: date | None = None
    weekdays: list[int] | None = None

class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    origin_token: str
    destination_token: str
    effective_from: date
    weekdays: list[int] = Field(min_length=1,max_length=7)
    buffer_minutes: int = Field(ge=0,le=120)
    mode: Literal['driving','transit'] = 'driving'
    test_only: bool = False

async def create_draft(db,runtime,settings,user,run,parsed,text):
    intent=CommuteIntent.model_validate(parsed['commute'])
    timezone=(run.context_json or {}).get('timezone') or settings.matter_default_timezone
    draft=MatterDraft(user_id=user.user_id,ai_run_id=run.local_run_id,kind='create',status='preview',version=1,payload_json={})
    db.add(draft);await db.flush()
    # Persist identity before any upstream request; recovery finds the same draft.
    state={'intent':intent.model_dump(mode='json'),'timezone':timezone,'status':'locating'}
    draft.payload_json={'kind':'create','title':'日常通勤安排','goal':text,'source_text':text,'schedule':None,'todos':[], 'complete_todo_ids':[], 'matter_action':'none','commute':state}
    await db.commit()
    for side,kind in [('origin','place'),('destination','address')]:
        state[side+'_search']=await commute_places.search_places(runtime,secret=settings.tool_callback_token.get_secret_value(),user_id=user.user_id,draft_id=draft.draft_id,query=getattr(intent,side+'_query'),city=intent.city,search_type=kind,limit=5)
        draft.payload_json={**draft.payload_json,'commute':dict(state)};await db.commit()
    state['status']='needs_selection'
    draft.payload_json={**draft.payload_json,'commute':state};await db.commit()
    return draft

def selected_place(token,settings,user,draft):
    try:place=commute_places.verify_selection(token,settings.tool_callback_token.get_secret_value())
    except ValueError as exc:raise InvalidRequestError('place_selection_invalid',str(exc)) from None
    if place.get('user_id')!=user.user_id or place.get('draft_id')!=draft.draft_id:
        raise InvalidRequestError('place_selection_invalid','地点选择不属于当前账号与草稿。')
    if place.get('coordinate_system')!='gcj02':raise InvalidRequestError('coordinate_system_mismatch','需要高德坐标。')
    place["selection_token"] = token
    return place

async def quote(db,runtime,settings,user,draft_id,request):
    from assistant_api.services import matters
    draft=await matters._get_draft(db,user,draft_id)
    if draft.status!='preview' or draft.version!=request.expected_version:raise ConflictError('preview_stale','请刷新草稿后再试。')
    state=dict((draft.payload_json or {}).get('commute') or {})
    if not state:raise InvalidRequestError('not_commute_draft','不是通勤组合草稿。')
    zone=ZoneInfo(state['timezone'])
    if request.effective_from<datetime.now(zone).date():raise InvalidRequestError('past_first_date','首次日期不能早于今天。')
    if any(x<1 or x>7 for x in request.weekdays) or request.effective_from.isoweekday() not in request.weekdays:
        raise InvalidRequestError('weekday_invalid','首次日期应包含在重复星期中。')
    origin=selected_place(request.origin_token,settings,user,draft);dest=selected_place(request.destination_token,settings,user,draft)
    arrival=datetime.fromisoformat(f"{request.effective_from}T{state['intent']['arrival_local_time']}:00").replace(tzinfo=zone)
    if arrival<=datetime.now(zone):raise InvalidRequestError('arrival_passed','首次到达时间已过去，请选未来日期。')
    inputs={'origin':origin,'destination':dest,'effective_from':str(request.effective_from),'weekdays':sorted(set(request.weekdays)),'buffer_minutes':request.buffer_minutes,'test_only':request.test_only}
    # Mode changes reuse the same two measurements; never re-call providers to
    # repaint the preview. An interrupted provider call is not silently retried.
    if state.get('quote_inputs')!=inputs:
        claim=await db.execute(update(MatterDraft).where(MatterDraft.draft_id==draft_id,MatterDraft.user_id==user.user_id,MatterDraft.version==request.expected_version,MatterDraft.status=='preview').values(version=request.expected_version+1))
        if claim.rowcount!=1:
            await db.rollback();raise ConflictError('preview_stale','已有测算正在处理，请刷新。')
        if state.get('quote_status')=='measuring':raise ConflictError('quote_outcome_unknown','测算尚未完成，不能重复调用。')
        state.update(quote_inputs=inputs,quote_status='measuring',routes={},route_quote_id=new_id(),expires_at=(datetime.now(UTC)+timedelta(minutes=30)).isoformat())
        draft.payload_json={**draft.payload_json,'commute':dict(state)};await db.commit()
        for mode in ['driving','transit']:
            state['routes'][mode]=await commute_estimate.estimate(runtime,origin=(origin['latitude'],origin['longitude']),destination=(dest['latitude'],dest['longitude']),mode=mode,depart_at=arrival-timedelta(hours=1),arrive_by=arrival,origin_city=origin['city'],destination_city=dest['city'],origin_coordinate_system='gcj02',destination_coordinate_system='gcj02')
            draft.payload_json={**draft.payload_json,'commute':schedule_routes._jsonable(state)};await db.commit()
        state['quote_status']='complete'
    elif state.get('quote_status')!='complete':raise ConflictError('quote_outcome_unknown','请检查原测算结果，不重复请求。')
    route=state['routes'].get(request.mode,{})
    if route.get('status')!='available' or not route.get('duration_seconds'):
        state['selected_mode']=None;draft.payload_json={**draft.payload_json,'commute':schedule_routes._jsonable(state)};await db.commit()
        raise InvalidRequestError('selected_route_unavailable','所选方式没有真实可用路线，请查看结果并选择可用方式。')
    end=arrival-timedelta(minutes=request.buffer_minutes);start=end-timedelta(seconds=route['duration_seconds'])
    state.update(selected_mode=request.mode,status='quoted',suggested_departure_at=start.isoformat(),target_arrival_at=arrival.isoformat())
    schedule={'title':'日常通勤首日','starts_at':start.isoformat(),'ends_at':end.isoformat(),'location':dest['name'],'latitude':dest['latitude'],'longitude':dest['longitude'],'coordinate_system':'gcj02','location_city':dest['city'],'location_address':dest.get('address'),'location_source':dest.get('source'),'location_external_id':dest.get('provider_place_id')}
    draft.payload_json={**draft.payload_json,'schedule':schedule,'commute':schedule_routes._jsonable(state)}
    draft.version+=1
    draft.conflict_json=schedule_routes._jsonable(await matters._conflict_for_payload(db,runtime,user,draft.payload_json,settings=settings,timezone=state['timezone']))
    await db.commit();await db.refresh(draft)
    return matters._serialize_draft(draft)

def validate_quote(payload):
    state=payload.get('commute')
    if not state:return
    if state.get('status')!='quoted' or state.get('quote_status')!='complete':raise InvalidRequestError('commute_quote_required','请先确认地点并比较真实路线。')
    if datetime.fromisoformat(state['expires_at'])<datetime.now(UTC):raise ConflictError('route_quote_expired','路线报价已过期，请重新测算。')
    schedule=payload.get('schedule') or {};inp=state['quote_inputs'];dest=inp['destination']
    if any(schedule.get(k)!=dest.get(k) for k in ['latitude','longitude']) or schedule.get('starts_at')!=state['suggested_departure_at']:
        # Compare instants, since draft normalization can canonicalize ISO offsets.
        same_start=schedule.get('starts_at') and datetime.fromisoformat(schedule['starts_at'].replace('Z','+00:00'))==datetime.fromisoformat(state['suggested_departure_at'])
        if not same_start or any(schedule.get(k)!=dest.get(k) for k in ['latitude','longitude']):raise ConflictError('route_quote_mismatch','日程与测算条件不一致，请重新测算。')
    if schedule.get('location') != dest['name'] or schedule.get('coordinate_system') != 'gcj02' or schedule.get('location_address') != dest.get('address'):
        raise ConflictError('route_quote_mismatch','目的地已变更，请重新测算。')
    expected_end=datetime.fromisoformat(state['target_arrival_at'])-timedelta(minutes=inp['buffer_minutes'])
    if not schedule.get('ends_at') or datetime.fromisoformat(schedule['ends_at'].replace('Z','+00:00'))!=expected_end:raise ConflictError('route_quote_mismatch','到达时间已变更。')

async def attach_records(db,user,draft,matter,schedule):
    state=draft.payload_json['commute'];inp=state['quote_inputs'];origin=inp['origin'];dest=inp['destination']
    plan=CommutePlan(user_id=user.user_id,matter_id=matter.matter_id,first_schedule_id=schedule.schedule_id,effective_from=date.fromisoformat(inp['effective_from']),name=matter.title,enabled=True,timezone=state['timezone'],weekdays_json=inp['weekdays'],arrival_local_time=state['intent']['arrival_local_time'],check_local_time='07:00',transport_mode=state['selected_mode'],buffer_minutes=inp['buffer_minutes'],rain_probability_threshold=.4,rain_extra_buffer_minutes=0,notify_enabled=False,origin_label=origin['name'],origin_latitude=origin['latitude'],origin_longitude=origin['longitude'],destination_label=dest['name'],destination_latitude=dest['latitude'],destination_longitude=dest['longitude'],revision=1)
    db.add(plan);await db.flush()
    route={**state['routes'][state['selected_mode']],'route_quote_id':state['route_quote_id']}
    route['schedule_id']=schedule.schedule_id
    route['destination']={**(route.get('destination') or {}),'schedule_id':schedule.schedule_id,'basis':'destination_schedule'}
    await schedule_routes.save_for_schedule(db,user,schedule_id=schedule.schedule_id,result=route,commit=False)
    return plan.plan_id
