"""One continuous P0 acceptance on isolated SQLite and fixed Nexus decisions.

The scenario follows a contract and its original meeting through a due check,
receipt of the contract, rescheduling and pause. No external provider or notification is used.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import auth
from assistant_api.models import Matter, MatterTodo, ScheduleEvent


@pytest.mark.asyncio
async def test_contract_meeting_followup_lifecycle(harness):
    """Keep record identity through due follow-up, completion, reschedule and pause."""
    h = harness
    now = datetime(2030, 1, 2, 10, tzinfo=UTC)
    async with h.app.state.session_factory() as db:
        matter = Matter(user_id=h.user_a, title='合同面谈', goal='完成合同面谈')
        db.add(matter)
        await db.flush()
        todo = MatterTodo(user_id=h.user_a, matter_id=matter.matter_id, title='收到合同修订版', due_at=now)
        meeting = ScheduleEvent(source='web_native',status='scheduled',user_id=h.user_a, matter_id=matter.matter_id, title='合同面谈', starts_at=now+timedelta(days=1), location='会议室')
        db.add_all([todo, meeting])
        await db.commit()
        mid, tid, sid = matter.matter_id, todo.todo_id, meeting.schedule_id
    operations = h.app.openapi()['paths']
    from test_api_operation_flow import fixed_decisions

    def call(path, method, *, path_parameters=None, body=None):
        """Use the runtime-published operation ID rather than a tool registration."""
        return {'decision':'call','call':{'operation_id':operations[path][method]['operationId'],'path_parameters':path_parameters or {},'body':body or {}}}

    read_id = operations['/api/v1/matters/{matter_id}']['get']['operationId']
    contract_ref = {'name':'contract','operation_id':read_id,'path_parameters':{'matter_id':mid},'result_pointer':'/todos','match':{'todo_id':tid}}
    body = {'idempotency_key':'contract-followup-001','matter_id':mid,'title':'跟进合同修订版','instruction':'合同未收到时问我是否收到。','references':[contract_ref],
            'trigger':{'kind':'time','source':{'reference':'contract','field':'/due_at'}},
            'stop_when':[{'reference':'contract','field':'/status','operator':'eq','value':'done'}]}
    fixed_decisions(h,[{'decision':'search','search_query':'创建事项持续跟进 follow up'},call('/api/v1/follow-ups','post',body=body),{'decision':'answer','answer':'好，我会跟进合同修订版。'}])
    created = await h.client.post('/api/v1/matters/intake',headers=auth(h.token_a),json={'text':f'为事项{mid}跟进合同修订版，在待办截止时检查，收到后结束催促。','idempotency_key':'create-followup-chat-001'})
    assert created.status_code == 200, created.text
    rule = created.json()['pending_context']['execution_result']['data']
    replay=await h.client.post('/api/v1/follow-ups',headers=auth(h.token_a),json=body)
    assert replay.status_code==201 and replay.json()['follow_up_id']==rule['follow_up_id'], replay.text
    assert rule['next_trigger_at'] is not None
    from assistant_api.services import follow_ups
    assert await follow_ups.scheduler_tick(h.app,now=now) == 1
    # One meaningful question is delivered through existing intake history.
    from test_api_operation_flow import fixed_decisions
    fixed_decisions(h,[{'decision':'ask_user','question':'合同修订版收到了吗？','choices':['收到了','还没有']}])
    assert await follow_ups.worker_tick(h.app,now=now)
    runs = await h.client.get(f"/api/v1/follow-ups/{rule['follow_up_id']}/executions",headers=auth(h.token_a))
    assert runs.json()[0]['local_run_id']
    history = await h.client.get('/api/v1/matters/intake',headers=auth(h.token_a))
    assert any(item['clarify']=='合同修订版收到了吗？' for item in history.json())
    # Same event is not re-enqueued while awaiting the user's answer.
    assert await follow_ups.scheduler_tick(h.app,now=now) == 0
    fixed_decisions(h,[{'decision':'search','search_query':'完成待办 complete todo'},call('/api/v1/matters/{matter_id}/todos/{todo_id}/complete','post',path_parameters={'matter_id':mid,'todo_id':tid}),{'decision':'answer','answer':'好，合同这项已完成。','cards':[{'kind':'result','title':'合同修订版','blocks':[{'type':'fields','items':[{'label':'状态','value':{'$result':'/status'}}]}]}]}])
    completed = await h.client.post('/api/v1/matters/intake',headers=auth(h.token_a),json={'text':'合同收到了','idempotency_key':'contract-received-chat-001','continuation_run_id':runs.json()[0]['local_run_id']})
    assert completed.status_code == 200, completed.text
    assert completed.json()['pending_context']['cards'][0]['status']=='completed'
    linked=(await h.client.get(f"/api/v1/follow-ups/{rule['follow_up_id']}/executions",headers=auth(h.token_a))).json()[0]
    assert linked['state']=='succeeded' and linked['local_run_id']==completed.json()['local_run_id']
    await follow_ups.scheduler_tick(h.app,now=now)
    closed = await h.client.get(f"/api/v1/follow-ups/{rule['follow_up_id']}",headers=auth(h.token_a))
    assert closed.json()['state']=='closed'
    meeting_ref = {'name':'meeting','operation_id':read_id,'path_parameters':{'matter_id':mid},'result_pointer':'/schedules','match':{'schedule_id':sid}}
    body.update(idempotency_key='meeting-followup-001',title='面谈准备',references=[meeting_ref],trigger={'kind':'time','source':{'reference':'meeting','field':'/starts_at'},'offset_seconds':-3600},stop_when=[])
    upcoming = (await h.client.post('/api/v1/follow-ups',headers=auth(h.token_a),json=body)).json()
    fixed_decisions(h,[call('/api/v1/matters/{matter_id}','get',path_parameters={'matter_id':mid}),
        {'decision':'search','search_query':'创建原日程编辑草稿 create edit draft'},
        call('/api/v1/matters/{matter_id}/drafts','post',path_parameters={'matter_id':mid},body={'idempotency_key':'meeting-edit-001','schedule_id':{'$result':'/schedules','where':{'schedule_id':sid},'field':'schedule_id'}}),
        call('/api/v1/matters/drafts/{draft_id}','patch',path_parameters={'draft_id':{'$result':'/draft_id'}},body={'expected_version':{'$result':'/version'},'schedule':{'$result':'/payload/schedule','overrides':{'starts_at':'2030-01-03T12:00:00+00:00'}}}),
        {'decision':'search','search_query':'confirm draft 确认保存草稿'},
        call('/api/v1/matters/drafts/{draft_id}/confirm','post',path_parameters={'draft_id':{'$result':'/draft_id'}},body={'expected_version':{'$result':'/version'}}),
        {'decision':'answer','answer':'改好了，面谈改到十二点。','cards':[{'kind':'result','title':'面谈安排','blocks':[{'type':'fields','items':[{'label':'时间','value':{'$result':'/schedule/starts_at'}},{'label':'名称','value':{'$result':'/payload/schedule/title','step':2}}]}]}]}])
    changed=await h.client.post(f'/api/v1/matters/{mid}/intake',headers=auth(h.token_a),json={'text':'查询事项日程详情，将原面谈改到2030年1月3日12点UTC，保留原日程ID和其他字段。','idempotency_key':'meeting-reschedule-chat-001'})
    assert changed.status_code==200, changed.text
    assert changed.json()['pending_context']['cards'], changed.text
    assert changed.json()['pending_context']['cards'][0]['blocks'][0]['items'][0]['value'].startswith('2030-01-03T12:00')
    await follow_ups.scheduler_tick(h.app,now=now)
    moved = (await h.client.get(f"/api/v1/follow-ups/{upcoming['follow_up_id']}",headers=auth(h.token_a))).json()
    assert moved['next_trigger_at'] != upcoming['next_trigger_at']
    fixed_decisions(h,[{'decision':'search','search_query':'暂停跟进 update follow up'},call('/api/v1/follow-ups/{follow_up_id}','patch',path_parameters={'follow_up_id':moved['follow_up_id']},body={'expected_version':moved['version'],'state':'paused'}),{'decision':'answer','answer':'好，先暂停跟进。'}])
    paused = await h.client.post(f'/api/v1/matters/{mid}/intake',headers=auth(h.token_a),json={'text':'暂停面谈准备的跟进，不用再催了。','idempotency_key':'pause-followup-chat-001'})
    assert paused.status_code == 200, paused.text
    assert await follow_ups.scheduler_tick(h.app,now=now+timedelta(days=2)) == 0
    async with h.app.state.session_factory() as db:
        schedules = list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.matter_id==mid)))
        assert [s.schedule_id for s in schedules]==[sid]

    # The same generic planner calls the existing commute estimate interface.
    fixed_decisions(h,[{'decision':'search','search_query':'通勤路线测算 estimate'},call('/api/v1/commute/estimate','post',body={'origin':{'latitude':31.2,'longitude':121.4},'destination':{'latitude':31.3,'longitude':121.5},'mode':'driving','depart_at':'2030-01-03T11:00:00+00:00'}),{'decision':'answer','answer':'出行建议整理好了。','cards':[{'kind':'result','title':'出行建议','blocks':[{'type':'fields','items':[{'label':'预计用时','value':{'$result':'/duration_seconds'},'format':'number'}]}]}]}])
    import json
    import httpx
    transport=h.app.state.nexus_client._client._transport
    final_workflow=h.fake.workflow_seq+3

    class PublicDeltaStream(httpx.AsyncByteStream):
        """Insert one public model delta into the final answer's real SSE protocol."""
        def __init__(self, stream):
            """Retain the existing finite workflow stream."""
            self.stream=stream

        async def __aiter__(self):
            """Yield the started frame, answer JSON fragment and original terminal frame."""
            emitted=False
            async for chunk in self.stream:
                yield chunk
                if not emitted:
                    data={'event_type':'agent.answer.delta','public_payload':{'text_delta':'{"decision":"answer","answer":"出行建议整理好了。"}'}}
                    yield ('event: agent.answer.delta\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode()
                    emitted=True

    async def supply_public_delta(request):
        """Keep all normal protocol requests, adding a delta only to the final answer turn."""
        response=await transport.handle_async_request(request)
        if request.url.path.endswith('/agent-workflows') and h.fake.workflow_seq==final_workflow:
            response.stream=PublicDeltaStream(response.stream)
        return response

    h.app.state.nexus_client._client._transport=httpx.MockTransport(supply_public_delta)
    route=await h.client.post('/api/v1/matters/intake/stream',headers=auth(h.token_a),json={'text':'按我给出的起终点坐标测算通勤路线，出行方式驾车，出发时间2030年1月3日11点UTC。','idempotency_key':'commute-api-chat-001'})
    assert route.status_code==200,route.text
    assert 'event: answer' in route.text and '出行建议整理好了。' in route.text
    frames=[frame for frame in route.text.split('\n\n') if frame.startswith('event: result')]
    assert len(frames)==1,route.text
    final=json.loads(frames[0].split('data: ',1)[1])
    assert final['pending_context']['execution_result']['status']=='succeeded'
    assert final['pending_context']['cards'],final

    # Record-change triggers use the same meeting; ORM bulk SQL publishes an event too.
    from sqlalchemy import update
    watch_body={**body,'idempotency_key':'meeting-location-watch-001','title':'面谈地点变化','instruction':'查询原事项日程详情，地点变化时展示新的面谈安排。','references':[meeting_ref],'trigger':{'kind':'record_changed','reference':'meeting','fields':['/location']},'stop_when':[]}
    watch=await h.client.post('/api/v1/follow-ups',headers=auth(h.token_a),json=watch_body)
    assert watch.status_code==201,watch.text
    async with h.app.state.session_factory() as db:
        await db.execute(update(ScheduleEvent).where(ScheduleEvent.schedule_id==sid).values(location='第二会议室'))
        await db.commit()
    assert await follow_ups.scheduler_tick(h.app,now=now)==1
    fixed_decisions(h,[call('/api/v1/matters/{matter_id}','get',path_parameters={'matter_id':mid}),{'decision':'answer','answer':'面谈地点更新了。','cards':[{'kind':'result','title':'面谈安排','blocks':[{'type':'fields','items':[{'label':'地点','value':{'$result':'/schedules','where':{'schedule_id':sid},'field':'location'}}]}]}]}])
    assert await follow_ups.worker_tick(h.app,now=now)
    watched=(await h.client.get(f"/api/v1/follow-ups/{watch.json()['follow_up_id']}/executions",headers=auth(h.token_a))).json()[0]
    projected=(await h.client.get('/api/v1/matters/intake/'+watched['local_run_id'],headers=auth(h.token_a))).json()
    assert projected['pending_context']['cards'][0]['blocks'][0]['items'][0]['value']=='第二会议室'
    assert await follow_ups.scheduler_tick(h.app,now=now)==0
    async with h.app.state.session_factory() as db:
        await db.execute(update(ScheduleEvent).where(ScheduleEvent.schedule_id==sid).values(location='第三会议室'))
        await db.commit()
    assert await follow_ups.scheduler_tick(h.app,now=now)==1
    fixed_decisions(h,[{'decision':'answer','answer':'没有需要补充的安排。','notify':False}])
    assert await follow_ups.worker_tick(h.app,now=now)
    records=(await h.client.get(f"/api/v1/follow-ups/{watch.json()['follow_up_id']}/executions",headers=auth(h.token_a))).json()
    quiet_id=records[0]['local_run_id']
    assert quiet_id not in [item['local_run_id'] for item in (await h.client.get('/api/v1/matters/intake',headers=auth(h.token_a))).json()]


@pytest.mark.asyncio
async def test_followup_resumes_after_saved_write_without_writing_twice(harness, monkeypatch):
    """Resume a worker stopped after its write receipt committed, preserving the single write."""
    from assistant_api.models import FollowUpExecution
    from assistant_api.services import follow_ups, operation_intake
    from test_api_operation_flow import add_future_interface, fixed_decisions, widget_call
    h=harness
    now=datetime(2030,1,2,10,tzinfo=UTC)
    writes=add_future_interface(h)
    async with h.app.state.session_factory() as db:
        matter=Matter(user_id=h.user_a,title='合同资料修订',goal='修订合同资料')
        db.add(matter);await db.commit();mid=matter.matter_id
    read_id=h.app.openapi()['paths']['/api/v1/matters/{matter_id}']['get']['operationId']
    body={'idempotency_key':'recover-followup-001','matter_id':mid,'title':'修订资料名称','execution_mode':'execute','instruction':'修改测试物件名称','references':[{'name':'matter','operation_id':read_id,'path_parameters':{'matter_id':mid}}],'trigger':{'kind':'time','at':now.isoformat()}}
    response=await h.client.post('/api/v1/follow-ups',headers=auth(h.token_a),json=body)
    assert response.status_code==201,response.text
    rid=response.json()['follow_up_id']
    assert await follow_ups.scheduler_tick(h.app,now=now)==1
    fixed_decisions(h,[widget_call()])
    original_save=operation_intake.save_result

    class WorkerStopped(BaseException):
        """Model process termination at a persisted execution boundary."""

    async def save_then_stop(db,row,flow,outcome,**fields):
        """Stop only after the real business write and its receipt have been committed."""
        result=await original_save(db,row,flow,outcome,**fields)
        if flow['state']=='executed':
            raise WorkerStopped()
        return result

    monkeypatch.setattr(operation_intake,'save_result',save_then_stop)
    with pytest.raises(WorkerStopped):
        await follow_ups.worker_tick(h.app,now=now,worker_id='worker-before-stop')
    assert writes==[('widget-001','新名称')]
    monkeypatch.setattr(operation_intake,'save_result',original_save)
    fixed_decisions(h,[{'decision':'answer','answer':'改好了。','cards':[{'kind':'result','title':'资料名称','blocks':[{'type':'fields','items':[{'label':'名称','value':{'$result':'/title','step':0}}]}]}]}])
    assert await follow_ups.worker_tick(h.app,now=now+timedelta(minutes=11),worker_id='worker-after-stop')
    assert writes==[('widget-001','新名称')]
    async with h.app.state.session_factory() as db:
        execution=await db.scalar(select(FollowUpExecution).where(FollowUpExecution.follow_up_id==rid))
        assert execution.state=='succeeded'
        assert execution.result_json['pending_context']['cards'][0]['blocks'][0]['items'][0]['value']=='新名称'


    # A pause committed by another request is read before the pending AI call dispatches.
    body['idempotency_key']='pause-before-call-001'
    other=await h.client.post('/api/v1/follow-ups',headers=auth(h.token_a),json=body)
    assert other.status_code==201,other.text
    other_rule=other.json()
    assert await follow_ups.scheduler_tick(h.app,now=now+timedelta(minutes=12))==1

    async def pause_before_call():
        """Pause in another API session while the model is returning its next operation."""
        paused=await h.client.patch('/api/v1/follow-ups/'+other_rule['follow_up_id'],headers=auth(h.token_a),json={'expected_version':other_rule['version'],'state':'paused'})
        assert paused.status_code==200,paused.text
        return widget_call()

    fixed_decisions(h,[pause_before_call])
    assert await follow_ups.worker_tick(h.app,now=now+timedelta(minutes=12),worker_id='pause-aware-worker')
    assert writes==[('widget-001','新名称')]
    async with h.app.state.session_factory() as db:
        stopped=await db.scalar(select(FollowUpExecution).where(FollowUpExecution.follow_up_id==other_rule['follow_up_id']))
        assert stopped.state=='skipped'


@pytest.mark.asyncio
async def test_additive_upgrade_and_generic_commute_background(harness, tmp_path):
    """Upgrade an old-schema fixture and run commute follow-up through generic background entrypoints."""
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    import httpx
    from sqlalchemy import insert, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from assistant_api.models import Base, User, CommutePlan, FollowUp, FollowUpExecution
    from assistant_api.main import create_app
    from assistant_api.background import tick
    from test_api_operation_flow import fixed_decisions
    h=harness
    url='sqlite+aiosqlite:///'+str(tmp_path/'pre-followup.db')
    engine=create_async_engine(url)
    old_tables=[table for table in Base.metadata.sorted_tables if table.name not in {'follow_ups','follow_up_executions','business_change_events'}]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync,tables=old_tables))
        await conn.execute(insert(User).values(user_id='migration-user',email='migration@example.com',display_name='临时验收',password_verifier='fixture',status='active'))
        await conn.execute(insert(Matter).values(matter_id='migration-matter',user_id='migration-user',title='通勤',goal='按时到达'))
    await engine.dispose()
    spec=importlib.util.spec_from_file_location('followup_sqlite_upgrade',Path(__file__).resolve().parents[1]/'scripts/migrate_sqlite.py')
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    await migration.upgrade(url)
    settings=h.app.state.settings.model_copy(update={'database_url':url,'allow_schema_create':False,'commute_auto_ai_enabled':True,'commute_auto_weather_enabled':False})
    app=create_app(settings,nexus_transport=httpx.MockTransport(h.fake.handler))
    now=datetime(2030,1,2,10,tzinfo=UTC)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as db:
            assert (await db.get(Matter,'migration-matter')).title=='通勤'
            assert await db.scalar(text("SELECT count(*) FROM schema_migrations WHERE version='0021_follow_ups'"))==1
            plan=CommutePlan(user_id='migration-user',matter_id='migration-matter',name='通勤',timezone='UTC',weekdays_json=[1,2,3,4,5,6,7],arrival_local_time='23:59',check_local_time='09:00',transport_mode='driving',origin_label='起点',origin_latitude=31.2,origin_longitude=121.4,destination_label='终点',destination_latitude=31.3,destination_longitude=121.5,notify_enabled=False)
            db.add(plan);await db.commit();pid=plan.plan_id
        assert await tick(app,'scheduler','acceptance-scheduler',now=now)==1
        paths=app.openapi()['paths']
        fixed_decisions(SimpleNamespace(app=app,fake=h.fake),[
            {'decision':'search','search_query':'查询通勤计划 commute plan'},
            {'decision':'call','call':{'operation_id':paths['/api/v1/commute-plans/{plan_id}']['get']['operationId'],'path_parameters':{'plan_id':pid}}},
            {'decision':'answer','answer':'今天按保存的通勤安排出行。','cards':[{'kind':'result','title':'通勤安排','blocks':[{'type':'fields','items':[{'label':'名称','value':{'$result':'/name'}}]}]}]}])
        assert await tick(app,'worker','acceptance-worker',now=now)
        async with app.state.session_factory() as db:
            rule=await db.scalar(select(FollowUp))
            execution=await db.scalar(select(FollowUpExecution))
            assert rule.state=='closed' and execution.state=='succeeded'
            assert execution.result_json['source']=='follow_up'
            assert execution.result_json['follow_up_id']==rule.follow_up_id
            assert execution.result_json['pending_context']['cards'][0]['blocks'][0]['items'][0]['value']=='通勤'
        assert await tick(app,'scheduler','acceptance-scheduler',now=now)==0
