"""Targeted rejection and graceful fallback scenarios for Today's recommendation."""
from datetime import UTC, datetime, timedelta

import pytest

from assistant_api.models import Matter, ScheduleEvent, ScheduleRouteResult, User
from assistant_api.services import next_actions, schedule_routes
from conftest import auth


@pytest.mark.asyncio
async def test_bad_feedback_and_unusable_route_keep_original_records(harness, monkeypatch):
    """Fail closed on invalid commands and only recommend a departure backed by a current route."""
    h = harness
    now = datetime(2031, 1, 8, 1, tzinfo=UTC)
    monkeypatch.setattr(next_actions, 'server_now', lambda: now)
    headers = auth(h.token_a)
    await h.client.put('/api/v1/availability', headers=headers, json={
        'expected_version':0,'timezone':'Asia/Shanghai',
        'windows':[{'weekday':now.isoweekday(),'starts_local':'08:00','ends_local':'18:00','kind':'available'}]})
    todo = (await h.client.post('/api/v1/todos',headers=headers,json={
        'idempotency_key':'next-action-failure-todo','title':'原待办','estimated_duration_minutes':15})).json()
    async with h.app.state.session_factory() as db:
        user = await db.get(User,h.user_a)
        matter = Matter(user_id=h.user_a, title='会面')
        db.add(matter)
        await db.flush()
        schedule = ScheduleEvent(user_id=h.user_a, matter_id=matter.matter_id,title='会面',
            starts_at=now+timedelta(minutes=35),ends_at=now+timedelta(hours=1),
            location='图书馆',source='web_native',latitude=31.3,longitude=121.5,coordinate_system='gcj02')
        db.add(schedule)
        await db.commit()
        sid = schedule.schedule_id
        saved = {'status':'available','duration_seconds':2100,'mode':'walking',
                 'destination':{'latitude':31.3,'longitude':121.5,'coordinate_system':'gcj02'},
                 'target_arrival_at':schedule.starts_at, 'timing_basis':'planned_time_not_live_forecast'}
        await schedule_routes.save_for_schedule(db,user,schedule_id=sid,result={**saved,'duration_seconds':True})

    async def query():
        """Read Today recommendation without contacting Nexus or the route provider."""
        response = await h.client.post('/api/v1/next-actions/query',headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    fallback = await query()
    assert fallback['primary']['kind'] == 'todo'
    assert fallback['alternatives'][0]['route_status'] == 'unavailable'
    async with h.app.state.session_factory() as db:
        await schedule_routes.save_for_schedule(db,await db.get(User,h.user_a),schedule_id=sid,result=saved)
    async with h.app.state.session_factory() as db:
        from sqlalchemy import select
        row = await db.scalar(select(ScheduleRouteResult).where(ScheduleRouteResult.schedule_id==sid))
        row.calculated_at=now
        await db.commit()
    departure = await query()
    assert departure['primary']['state']=='departure_due'
    assert departure['primary']['record_id']==sid
    assert departure['primary']['destination']=='图书馆'
    command={'recommendation_id':departure['recommendation_id'],'expected_recommendation_version':departure['version'],
             'kind':'schedule','record_id':sid,'action':'snooze','idempotency_key':'bad-next-action'}
    for args, token, status in [
        (command, h.token_a, 422),
        ({**command,'until':'2031-01-08T00:59:00Z'}, h.token_a, 422),
        ({**command,'until':'2031-01-08T01:05:00Z'}, h.token_b, 404),
        ({**command,'record_id':'not-a-candidate','until':'2031-01-08T01:05:00Z'}, h.token_a, 404),
    ]:
        response = await h.client.post('/api/v1/next-actions/commands',headers=auth(token),json=args)
        assert response.status_code==status,response.text
    async with h.app.state.session_factory() as db:
        schedule = await db.get(ScheduleEvent,sid)
        schedule.starts_at += timedelta(minutes=30)
        schedule.ends_at += timedelta(minutes=30)
        await db.commit()
    response = await h.client.post('/api/v1/next-actions/commands',headers=headers,
        json={**command,'until':'2031-01-08T01:05:00Z'})
    assert response.status_code == 409,response.text
    newest = await query()
    assert newest['primary']['record_id']==todo['todo']['todo_id']
    assert newest['suppressed']==[]
    assert not h.fake.calls


@pytest.mark.asyncio
async def test_commute_revision_and_expired_route_never_reuse_old_departure(harness, monkeypatch):
    """Treat a stale commute revision, cancelled date or expired measurement as unavailable."""
    from sqlalchemy import select
    from assistant_api.models import CommuteInstance, CommutePlan

    h = harness
    now = datetime(2031,1,8,1,tzinfo=UTC)
    monkeypatch.setattr(next_actions,'server_now',lambda:now)
    headers = auth(h.token_a)
    invalid = await h.client.put('/api/v1/availability',headers=headers,json={
        'expected_version':0,'timezone':'Mars/City','windows':[]})
    assert invalid.status_code == 422
    async with h.app.state.session_factory() as db:
        matter = Matter(user_id=h.user_a,title='通勤计划')
        db.add(matter)
        await db.flush()
        schedule = ScheduleEvent(user_id=h.user_a,matter_id=matter.matter_id,title='去公司',
            starts_at=now-timedelta(minutes=2),ends_at=now+timedelta(minutes=18),
            location='公司',latitude=31.3,longitude=121.5,coordinate_system='gcj02',source='web_native')
        db.add(schedule)
        await db.flush()
        plan = CommutePlan(user_id=h.user_a,matter_id=matter.matter_id,first_schedule_id=schedule.schedule_id,
            name='通勤计划',timezone='Asia/Shanghai',weekdays_json=[now.isoweekday()],arrival_local_time='09:23',
            check_local_time='07:00',transport_mode='walking',buffer_minutes=5,origin_label='家',
            origin_latitude=31.2,origin_longitude=121.4,destination_label='公司',
            destination_latitude=31.3,destination_longitude=121.5,revision=3)
        db.add(plan)
        await db.flush()
        pid = plan.plan_id
        instance = CommuteInstance(user_id=h.user_a,plan_id=pid,plan_revision=3,local_service_date=now.date(),
            timezone='Asia/Shanghai',target_arrival_at=now+timedelta(minutes=23),check_window_start_at=now)
        db.add(instance)
        await schedule_routes.save_for_schedule(db,await db.get(User,h.user_a),schedule_id=schedule.schedule_id,
            result={'status':'available','duration_seconds':1200,'mode':'walking','target_arrival_at':instance.target_arrival_at,
                'origin':{'latitude':31.2,'longitude':121.4,'coordinate_system':'gcj02'},
                'destination':{'latitude':31.3,'longitude':121.5,'coordinate_system':'gcj02'}})
        row = await db.scalar(select(ScheduleRouteResult).where(ScheduleRouteResult.schedule_id==schedule.schedule_id))
        row.calculated_at=now
        iid, rid = instance.instance_id, row.route_result_id
        await db.commit()

    async def primary():
        """Read the actual classification from the public recommendation interface."""
        result = await h.client.post('/api/v1/next-actions/query',headers=headers)
        assert result.status_code==200,result.text
        return result.json()['primary']

    result = await primary()
    assert result['state']=='departure_due' and result['commute_plan_id']==pid
    assert result['departure_at'].startswith('2031-01-08T00:58')
    assert 'reschedule' not in result['actions']
    async with h.app.state.session_factory() as db:
        (await db.get(CommutePlan,pid)).revision=4
        await db.commit()
    assert await primary() is None
    async with h.app.state.session_factory() as db:
        i = await db.get(CommuteInstance,iid)
        i.plan_revision=4
        i.status='closed'
        await db.commit()
    assert await primary() is None
    async with h.app.state.session_factory() as db:
        (await db.get(CommuteInstance,iid)).status='active'
        (await db.get(ScheduleRouteResult,rid)).calculated_at=now-timedelta(hours=1)
        await db.commit()
    assert await primary() is None
    assert not h.fake.calls
