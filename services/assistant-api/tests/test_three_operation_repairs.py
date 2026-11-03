"""Focused acceptance for commute atomic updates, planner correction and result cards.

Only these newly defined cases are run. They use temporary SQLite and existing
explicit provider doubles; real Nexus evaluation is separately opt-in.
"""
import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from conftest import auth
from test_api_operation_flow import fixed_decisions
from assistant_api.models import CommuteInstance, CommutePlan, FollowUp, FollowUpExecution, MatterTodo, ScheduleEvent, User
from assistant_api.services import response_cards, schedule_routes, commute_lifecycle, follow_ups
from assistant_api.commute.services.scheduling import ensure_instance


async def seed_commute(h):
    """Save one synthetic owned commute basis without invoking a planning model."""
    created = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'repair-commute-basis', 'title': '测试上班通勤',
        'starts_at': '2030-11-04T08:20:00+08:00', 'ends_at': '2030-11-04T08:50:00+08:00',
        'location': '测试公司', 'latitude': 31.23, 'longitude': 121.47, 'coordinate_system': 'gcj02'})
    assert created.status_code == 201, created.text
    body = created.json()
    async with h.app.state.session_factory() as db:
        plan = CommutePlan(user_id=h.user_a, matter_id=body['matter']['matter_id'],
            first_schedule_id=body['schedule']['schedule_id'], name='测试上班通勤', enabled=True,
            timezone='Asia/Shanghai', weekdays_json=[1,2,3,4,5], arrival_local_time='09:00',
            check_local_time='07:00', transport_mode='driving', buffer_minutes=10,
            rain_probability_threshold=.4, rain_extra_buffer_minutes=10, notify_enabled=False,
            origin_label='测试家园', origin_latitude=31.22, origin_longitude=121.44,
            destination_label='测试公司', destination_latitude=31.23, destination_longitude=121.47,
            effective_from=date(2030,11,4), revision=1)
        db.add(plan); await db.commit(); await db.refresh(plan)
        body['plan_id'] = plan.plan_id
        from assistant_api.services.commute_estimate import estimate
        route=await estimate(h.app.state.commute_runtime,origin=(31.22,121.44),destination=(31.23,121.47),
            mode='driving',depart_at=datetime(2030,11,4,0,tzinfo=UTC),arrive_by=datetime(2030,11,4,1,tzinfo=UTC),
            origin_coordinate_system='gcj02',destination_coordinate_system='gcj02',schedule_id=plan.first_schedule_id)
        await schedule_routes.save_for_schedule(db,await db.get(User,h.user_a),schedule_id=plan.first_schedule_id,result=route)
    return body


@pytest.mark.asyncio
async def test_day_change_remeasures_same_schedule_and_survives_reconciliation(harness):
    """One command retains IDs, saves the new route and day override, and replays once."""
    h=harness; seeded=await seed_commute(h); pid=seeded['plan_id']
    path=f'/api/v1/commute-plans/{pid}/days'
    payload={'service_date':'2030-11-04','arrival_local_time':'10:00','expected_plan_revision':1,
             'expected_matter_version':1,'idempotency_key':'repair-day-change-001'}
    response=await h.client.patch(path,headers=auth(h.token_a),json=payload)
    assert response.status_code==200,response.text
    result=response.json();assert result['applied']
    assert result['schedule']['schedule_id']==seeded['schedule']['schedule_id']
    assert result['schedule']['starts_at']=='2030-11-04T01:20:00Z'
    assert result['schedule']['ends_at']=='2030-11-04T01:50:00Z'
    assert result['target_arrival_at']=='2030-11-04T02:00:00Z'
    assert result['route']['target_arrival_at']=='2030-11-04T02:00:00Z'
    replay=await h.client.patch(path,headers=auth(h.token_a),json=payload)
    assert replay.status_code==200 and replay.json()['replayed']
    async with h.app.state.session_factory() as db:
        plan=await db.get(CommutePlan,pid)
        assert plan.arrival_local_time=='09:00' and plan.revision==1
        instance=await ensure_instance(db,plan,now=datetime(2030,11,4,0,tzinfo=UTC),service_date=date(2030,11,4))
        assert instance.target_arrival_at.replace(tzinfo=UTC)==datetime(2030,11,4,2,tzinfo=UTC)
        schedules=list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.user_id==h.user_a,ScheduleEvent.matter_id==plan.matter_id)))
        assert len(schedules)==1
        actual=await schedule_routes.valid_for_schedule(db,await db.get(User,h.user_a),schedules[0])
        assert actual and actual['target_arrival_at']=='2030-11-04T02:00:00Z'


@pytest.mark.asyncio
async def test_past_day_is_explained_without_writing_and_generic_time_patch_is_rejected(harness):
    """Read reports an expired day; writes cannot bypass commute rules with a calendar end time."""
    h=harness;seeded=await seed_commute(h);pid=seeded['plan_id']
    path=f'/api/v1/commute-plans/{pid}/days'
    h.app.state.commute_runtime.now=lambda:datetime(2030,11,4,3,tzinfo=UTC)
    read=await h.client.post(path+'/query',headers=auth(h.token_a),json={'service_date':'2030-11-04'})
    assert read.status_code==200 and read.json()['status']=='past'
    response=await h.client.patch(path,headers=auth(h.token_a),json={
        'service_date':'2030-11-04','arrival_local_time':'10:00','expected_plan_revision':1,'expected_matter_version':1,'idempotency_key':'past-day-001'})
    assert response.status_code==409,response.text
    bypass=await h.client.patch(f"/api/v1/schedules/{seeded['schedule']['schedule_id']}",headers=auth(h.token_a),json={
        'expected_matter_version':1,'idempotency_key':'bypass-day-001','ends_at':'2030-11-04T10:00:00+08:00'})
    assert bypass.status_code==409,bypass.text
    actual=(await h.client.get(f"/api/v1/schedules/{seeded['schedule']['schedule_id']}",headers=auth(h.token_a))).json()
    assert actual['schedule']['ends_at']=='2030-11-04T00:50:00Z'


@pytest.mark.asyncio
async def test_evening_rule_is_due_at_22_before_service_day_and_is_not_duplicated(harness):
    """Existing follow-up scheduling queues the next working day's refresh the previous evening."""
    h=harness;await seed_commute(h)
    h.app.state.settings.commute_auto_ai_enabled=True
    before=datetime(2030,11,3,13,tzinfo=UTC)
    await commute_lifecycle.materialize_follow_up_rules(h.app,now=before)
    await commute_lifecycle.materialize_follow_up_rules(h.app,now=before)
    async with h.app.state.session_factory() as db:
        rules=list(await db.scalars(select(FollowUp).where(FollowUp.user_id==h.user_a)))
        evening=[r for r in rules if ':evening:' in r.idempotency_key]
        assert len(evening)==1
        assert evening[0].next_trigger_at.replace(tzinfo=UTC)==datetime(2030,11,3,14,tzinfo=UTC)
        assert '2030-11-04' in evening[0].instruction and 'refresh_commute_day' in evening[0].instruction
    assert await follow_ups.scheduler_tick(h.app,now=before,scheduler_id='repair-evening')==0
    assert await follow_ups.scheduler_tick(h.app,now=datetime(2030,11,3,14,tzinfo=UTC),scheduler_id='repair-evening')==1


@pytest.mark.asyncio
async def test_unknown_operation_returns_feedback_then_ai_asks_without_dispatch(harness):
    """An invented tool name gets a bounded correction turn instead of ending the request."""
    h=harness
    fixed_decisions(h,[{'decision':'call','call':{'operation_id':'search_tools','query_parameters':{'query':'修改通勤'}}},
                       {'decision':'ask_user','question':'想改成几点到达？只改某一天，还是长期调整？'}])
    response=await h.client.post('/api/v1/matters/intake',headers=auth(h.token_a),json={
        'text':'通勤往后挪一点','idempotency_key':'unknown-op-repair-001'})
    assert response.status_code==200,response.text
    assert response.json()['outcome']=='needs_clarification'
    prompts='\n'.join(c['body'] for c in h.fake.calls if c['path']=='/api/v1/app/runs')
    assert 'api_call_invalid' in prompts and 'dispatched' in prompts


def test_display_can_format_weekday_array_without_weakening_record_selection():
    """Presentation reads a whole collection while mutation parameters still require unique selection."""
    receipt={'status':'succeeded','http_status':200,'data':{'weekdays':[1,2,3,4,5]}}
    cards=response_cards.prepare_cards([{'kind':'result','title':'已更新','blocks':[{'type':'fields','items':[
        {'label':'生效星期','value':{'$result':'/weekdays'},'format':'weekdays'}]}]}],receipt)
    assert cards[0]['blocks'][0]['items'][0]['value']=='周一至周五'
    assert cards[0]['blocks'][0]['items'][0]['format']=='text'
    from assistant_api.services.api_operations import resolve_result_references
    with pytest.raises(ValueError):resolve_result_references({'$result':'/items'}, {'status':'succeeded','data':{'items':[{'id':'a'},{'id':'b'}]}})


@pytest.mark.asyncio
async def test_card_repair_after_success_does_not_repeat_business_write(harness):
    """A bad display definition is corrected using saved results, never by creating another todo."""
    h=harness
    fixed_decisions(h,[
        {'decision':'call','call':{'operation_id':'create_todo','body':{'title':'卡片修复测试','idempotency_key':'card-repair-save'}}},
        {'decision':'answer','answer':'记好了。','cards':[{'kind':'result','title':'已完成','blocks':[{'type':'unknown'}]}]},
        {'decision':'answer','answer':'记好了。','cards':[{'kind':'result','title':'已完成','blocks':[{'type':'fields','items':[{'label':'名称','value':{'$result':'/todo/title'}}]}]}]},
    ])
    response=await h.client.post('/api/v1/matters/intake',headers=auth(h.token_a),json={
        'text':'新增一个卡片修复测试待办','idempotency_key':'card-repair-intake'})
    assert response.status_code==200,response.text
    assert response.json()['pending_context']['cards'][0]['blocks'][0]['items'][0]['value']=='卡片修复测试'
    async with h.app.state.session_factory() as db:
        assert len(list(await db.scalars(select(MatterTodo).where(MatterTodo.user_id==h.user_a,MatterTodo.title=='卡片修复测试'))))==1


@pytest.mark.asyncio
async def test_refresh_next_day_creates_one_day_and_route_failure_preserves_it(harness):
    """Evening refresh creates the next date once, while failed remeasurement leaves it intact."""
    h=harness;seeded=await seed_commute(h);pid=seeded['plan_id']
    path=f'/api/v1/commute-plans/{pid}/days/refresh'
    response=await h.client.post(path,headers=auth(h.token_a),json={'service_date':'2030-11-05','idempotency_key':'evening-refresh-001'})
    assert response.status_code==200,response.text
    saved=response.json();assert saved['applied'] and saved['route']['target_arrival_at']=='2030-11-05T01:00:00Z'
    assert saved['schedule']['starts_at']=='2030-11-05T00:20:00Z'
    replay=await h.client.post(path,headers=auth(h.token_a),json={'service_date':'2030-11-05','idempotency_key':'evening-refresh-001'})
    assert replay.status_code==200 and replay.json()['replayed']
    assert replay.json()['schedule']['schedule_id']==saved['schedule']['schedule_id']
    h.app.state.commute_runtime.eta.fail=True
    failed=await h.client.post(path,headers=auth(h.token_a),json={'service_date':'2030-11-05','idempotency_key':'evening-refresh-failed'})
    assert failed.status_code==409,failed.text
    current=(await h.client.post(path.removesuffix('/refresh')+'/query',headers=auth(h.token_a),json={'service_date':'2030-11-05'})).json()
    assert current['schedule']==saved['schedule'] and current['version']==saved['version']


@pytest.mark.asyncio
async def test_recurring_update_immediately_remeasures_next_effective_day(harness):
    """A long-term configuration change also refreshes the next future commute without another API call."""
    h=harness;seeded=await seed_commute(h);pid=seeded['plan_id']
    original=(await h.client.get(f'/api/v1/commute-plans/{pid}',headers=auth(h.token_a))).json()
    body={k:v for k,v in original.items() if k not in ('id','revision','created_at','updated_at','matter_id','first_schedule_id')}
    body.update(arrival_local_time='09:30',buffer_minutes=15)
    response=await h.client.put(f'/api/v1/commute-plans/{pid}',headers={**auth(h.token_a),'If-Match':'1'},json=body)
    assert response.status_code==200,response.text
    actual=(await h.client.post(f'/api/v1/commute-plans/{pid}/days/query',headers=auth(h.token_a),json={'service_date':'2030-11-04'})).json()
    assert actual['revision']==2 and actual['schedule']['schedule_id']==seeded['schedule']['schedule_id']
    assert actual['schedule']['starts_at']=='2030-11-04T00:45:00Z'
    assert actual['route']['target_arrival_at']=='2030-11-04T01:30:00Z'


@pytest.mark.asyncio
async def test_override_migration_adds_nullable_column_preserving_existing_row(tmp_path):
    """Only the new day-override migration alters an isolated old-format table; existing values stay intact."""
    import importlib.util
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    spec=importlib.util.spec_from_file_location('day_migration','scripts/migrate_sqlite.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "old.db"}')
    async with engine.begin() as connection:
        await connection.execute(text('CREATE TABLE commute_instances (instance_id VARCHAR(36) PRIMARY KEY, target_arrival_at DATETIME)'))
        await connection.execute(text("INSERT INTO commute_instances VALUES ('existing','2030-11-04 01:00:00')"))
        for version,table,column,sqltype in migration.COLUMN_MIGRATIONS:
            if version=='0023_commute_day_override':
                await connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sqltype}'))
        row=(await connection.execute(text('SELECT instance_id,target_arrival_at,arrival_local_time_override FROM commute_instances'))).one()
        assert tuple(row)==('existing','2030-11-04 01:00:00',None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_matter_detail_returns_actual_linked_plan_id(harness):
    """The selected matter exposes its owned plan reference so AI never substitutes a matter ID."""
    h=harness;seeded=await seed_commute(h)
    response=await h.client.get(f"/api/v1/matters/{seeded['matter']['matter_id']}",headers=auth(h.token_a))
    assert response.status_code==200,response.text
    assert response.json().get('commute_plan_id')==seeded['plan_id']
