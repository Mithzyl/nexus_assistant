"""Focused regression checks for the two observed browser failures."""
from datetime import UTC, datetime, timedelta
import pytest
from assistant_api.models import User, ScheduleEvent
from assistant_api.services import schedule_routes
from assistant_api.services.matter_plan import _restrict_unverified_absence_answer
from test_calendar_route_shortcuts import create_matter, add_schedule
from conftest import auth


def test_memory_status_statement_is_not_schedule_absence():
    text = '存在已保存的记忆，本次未授权。本次没有创建或修改任何事项、日程。'
    assert _restrict_unverified_absence_answer(text, {}) == text
    assert _restrict_unverified_absence_answer('该时段没有任何安排。', {}) != '该时段没有任何安排。'


@pytest.mark.asyncio
async def test_noop_and_title_edit_preserve_route_time_edit_invalidates(harness):
    h = harness
    matter = await create_matter(h, '路线保留')
    sid = await add_schedule(h, matter_id=matter['matter_id'], title='通勤', starts_at=datetime.now(UTC)+timedelta(days=2))
    async with h.app.state.session_factory() as db:
        user = await db.get(User, h.user_a)
        await schedule_routes.save_for_schedule(db,user,schedule_id=sid,result={'status':'available','mode':'driving','duration_seconds':900,'timing_basis':'planned_time_not_live_forecast','shape':{'status':'missing','segments':[]}})
    for index, field in enumerate([None, 'title', 'starts_at']):
        draft=(await h.client.post(f"/api/v1/matters/{matter['matter_id']}/drafts",headers=auth(h.token_a),json={'idempotency_key':f'route-diff-check-{index}','schedule_id':sid})).json()
        if field:
            schedule=dict(draft['payload']['schedule'])
            schedule[field]='新标题' if field=='title' else (datetime.now(UTC)+timedelta(days=3)).isoformat()
            if field=='starts_at':schedule['ends_at']=(datetime.now(UTC)+timedelta(days=3,hours=1)).isoformat()
            response=await h.client.patch(f"/api/v1/matters/drafts/{draft['draft_id']}",headers=auth(h.token_a),json={'expected_version':draft['version'],'schedule':schedule})
            assert response.status_code==200,response.text
            draft=response.json()
        response=await h.client.post(f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",headers={**auth(h.token_a),'Idempotency-Key':f'confirm-route-diff-{index}'},json={'expected_version':draft['version'],'create_schedule':True,'acknowledge_conflict':True})
        assert response.status_code==200,response.text
        async with h.app.state.session_factory() as db:
            user=await db.get(User,h.user_a);schedule=await db.get(ScheduleEvent,sid)
            value,reason=await schedule_routes.validation_for_schedule(db,user,schedule)
            assert (value is not None)==(field!='starts_at'),reason

@pytest.mark.asyncio
async def test_commute_bundle_quote_confirm_is_atomic_and_replay_safe(harness):
    """Offline contract check only; acceptance uses real Chrome/providers."""
    from assistant_api.models import MatterDraft, CommutePlan
    from assistant_api.services.commute_places import sign_selection
    from sqlalchemy import select
    h=harness
    day=(datetime.now(UTC)+timedelta(days=5)).date()
    async with h.app.state.session_factory() as db:
        draft=MatterDraft(user_id=h.user_a,kind='create',status='preview',version=1,payload_json={'kind':'create','title':'组合通勤','goal':'测试','source_text':'测试','schedule':None,'todos':[],'complete_todo_ids':[],'matter_action':'none','commute':{'status':'needs_selection','timezone':'Asia/Shanghai','intent':{'origin_query':'起点','destination_query':'终点','city':'上海','arrival_local_time':'08:00','buffer_minutes':10}}})
        db.add(draft);await db.commit();did=draft.draft_id
    point={'name':'测试地点','address':'测试地址','city':'上海','latitude':31.22,'longitude':121.44,'coordinate_system':'gcj02','source':'amap_place'}
    token=sign_selection(point,h.settings.tool_callback_token.get_secret_value(),user_id=h.user_a,draft_id=did,selection_kind='place')
    body={'expected_version':1,'origin_token':token,'destination_token':token,'effective_from':str(day),'weekdays':[day.isoweekday()],'buffer_minutes':10,'mode':'driving','test_only':True}
    foreign=await h.client.post(f'/api/v1/matters/drafts/{did}/commute-quote',headers=auth(h.token_b),json=body)
    assert foreign.status_code==404
    result=await h.client.post(f'/api/v1/matters/drafts/{did}/commute-quote',headers=auth(h.token_a),json=body)
    assert result.status_code==200,result.text
    draft=result.json();assert set(draft['payload']['commute']['routes'])=={'driving','transit'}
    for _ in range(2):
        response=await h.client.post(f'/api/v1/matters/drafts/{did}/confirm',headers={**auth(h.token_a),'Idempotency-Key':'bundle-confirm-once'},json={'expected_version':draft['version'],'create_schedule':True,'acknowledge_conflict':True})
        assert response.status_code==200,response.text
    async with h.app.state.session_factory() as db:
        plans=list((await db.scalars(select(CommutePlan).where(CommutePlan.user_id==h.user_a))).all())
        assert len(plans)==1
        plan=plans[0];assert plan.effective_from==day
        schedule=await db.get(ScheduleEvent,plan.first_schedule_id)
        assert schedule.matter_id==plan.matter_id
        route,reason=await schedule_routes.validation_for_schedule(db,await db.get(User,h.user_a),schedule)
        assert route and route['route_quote_id'],reason
        assert route['destination']['schedule_id']==schedule.schedule_id
        stored=await db.get(MatterDraft,did)
        assert stored.result_json.get('memory_local_run_id'),stored.result_json

@pytest.mark.asyncio
async def test_linked_plan_materialization_and_stop(harness):
    from assistant_api.models import CommutePlan
    from assistant_api.services.commute_lifecycle import generate_day
    from assistant_api.services import matters
    from assistant_api.core.errors import ConflictError
    h=harness
    matter=await create_matter(h,'通勤生命周期')
    start=datetime.now(UTC)+timedelta(days=2)
    sid=await add_schedule(h,matter_id=matter['matter_id'],title='首日',starts_at=start)
    async with h.app.state.session_factory() as db:
        plan=CommutePlan(user_id=h.user_a,matter_id=matter['matter_id'],first_schedule_id=sid,effective_from=start.date(),name='通勤',enabled=True,timezone='Asia/Shanghai',weekdays_json=[1,2,3,4,5,6,7],arrival_local_time='08:00',check_local_time='07:00',transport_mode='driving',buffer_minutes=10,origin_label='起点',origin_latitude=31.22,origin_longitude=121.44,destination_label='终点',destination_latitude=31.23,destination_longitude=121.47,revision=1,notify_enabled=False)
        db.add(plan);await db.commit();user=await db.get(User,h.user_a)
        target=(start+timedelta(days=1)).date()
        one=await generate_day(db,h.app.state.commute_runtime,user,plan.plan_id,target)
        db.expire_all()
        await db.refresh(plan);await db.refresh(user)
        generated=await db.get(ScheduleEvent,one['schedule_id'])
        assert generated.ends_at.replace(tzinfo=UTC).hour == 23
        assert (await schedule_routes.validation_for_schedule(db,user,generated))[1] is None
        await db.refresh(plan);await db.refresh(user)
        two=await generate_day(db,h.app.state.commute_runtime,user,plan.plan_id,target)
        assert one['schedule_id']==two['schedule_id'] and two['replayed']
        await matters.set_status(db,user,matter['matter_id'],status='archived',expected_version=matter['version'],cancel_reminders=True)
        await db.refresh(plan);assert not plan.enabled
        with pytest.raises(ConflictError):await generate_day(db,h.app.state.commute_runtime,user,plan.plan_id,target+timedelta(days=1))

@pytest.mark.asyncio
async def test_commute_revision_preserves_todo_datetime_and_existing_schedule(harness):
    from types import SimpleNamespace
    from sqlalchemy import select
    from assistant_api.models import CommutePlan
    from assistant_api.services.commute_lifecycle import revision_draft
    from assistant_api.services.matter_plan import validate_result
    h=harness
    await test_commute_bundle_quote_confirm_is_atomic_and_replay_safe(h)
    async with h.app.state.session_factory() as db:
        plan=await db.scalar(select(CommutePlan).where(CommutePlan.user_id==h.user_a))
        user=await db.get(User,h.user_a)
        parsed={'matter_id':plan.matter_id,'commute_update':{'service_date':str(plan.effective_from),'arrival_local_time':'08:30'},'todos':[{'title':'加油','due_at':datetime.now(UTC)+timedelta(hours=3),'reminder_at':datetime.now(UTC)+timedelta(hours=2)}]}
        draft=await revision_draft(db,h.app.state.commute_runtime,h.settings,user,SimpleNamespace(local_run_id=None),parsed,'修改通勤并提醒加油')
        assert draft.base_schedule_id==plan.first_schedule_id
        assert draft.payload_json['commute_revision']['status']=='ready'
        assert isinstance(draft.payload_json['todos'][0]['due_at'],str)
