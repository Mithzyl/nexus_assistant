from datetime import UTC, datetime
import pytest
from assistant_api.models import Matter, MatterDraft, NexusRun, User
from assistant_api.services import matters, matter_plan

@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed,lifecycle',[(True,'active'),(True,'archived'),(True,'deleted'),(False,'archived'),(False,'deleted')])
async def test_history_confirmation_survives_lifecycle(harness, confirmed, lifecycle):
    h=harness
    async with h.app.state.session_factory() as db:
        user=await db.get(User,h.user_a)
        matter=Matter(user_id=h.user_a,title='历史测试',status=lifecycle,version=1,deleted_at=datetime.now(UTC) if lifecycle=='deleted' else None)
        db.add(matter);await db.flush()
        run=NexusRun(user_id=h.user_a,purpose=matter_plan.PURPOSE,status='succeeded',idempotency_key='history-test',request_text='private source',context_json={'text':'private source',matters.INTAKE_RESULT_KEY:{'outcome':'draft'}})
        db.add(run);await db.flush()
        draft=MatterDraft(user_id=h.user_a,matter_id=matter.matter_id,ai_run_id=run.local_run_id,kind='update',status='confirmed' if confirmed else 'preview',version=1,payload_json={'title':'历史测试'})
        db.add(draft);await db.commit()
        result=await matters.get_intake_result(db,user,run.local_run_id)
        assert result['confirm_status']==('confirmed' if confirmed else 'unconfirmed')
        assert result['target_lifecycle']==lifecycle
        assert result['outcome']=='draft'
        if lifecycle=='deleted':
            assert result['draft'] is None and result['history_target_id'] is None
            assert 'private source' not in result['request_text']

@pytest.mark.asyncio
async def test_revoked_source_keeps_confirmation_without_private_details(harness):
    from assistant_api.models import ContentSource, ScheduleEvent
    h=harness
    async with h.app.state.session_factory() as db:
        user=await db.get(User,h.user_a)
        source=ContentSource(user_id=h.user_a,source_type='calendar',device_id='test',display_name='source',assistant_enabled=False)
        matter=Matter(user_id=h.user_a,title='secret title',status='active',version=1)
        db.add_all([source,matter]);await db.flush()
        schedule=ScheduleEvent(user_id=h.user_a,matter_id=matter.matter_id,title='secret',starts_at=datetime.now(UTC),data_source_id=source.source_id)
        run=NexusRun(user_id=h.user_a,purpose=matter_plan.PURPOSE,status='succeeded',idempotency_key='revoked',request_text='secret',context_json={'text':'secret',matters.INTAKE_RESULT_KEY:{'outcome':'draft'}})
        db.add_all([schedule,run]);await db.flush()
        db.add(MatterDraft(user_id=h.user_a,matter_id=matter.matter_id,base_schedule_id=schedule.schedule_id,ai_run_id=run.local_run_id,kind='update',status='confirmed',version=1,payload_json={'title':'secret'}));await db.commit()
        result=await matters.get_intake_result(db,user,run.local_run_id)
        assert result['confirm_status']=='confirmed' and result['target_lifecycle']=='source_revoked'
        assert result['draft'] is None and result['target_title'] is None and result['history_target_id'] is None
        assert result['request_text']=='历史操作'

@pytest.mark.asyncio
async def test_rescheduled_acknowledged_reminder_preserves_calendar_link(harness):
    from datetime import timedelta
    from sqlalchemy import select
    from assistant_api.models import ScheduleEvent, MatterTodo, Reminder
    from conftest import auth
    h=harness
    async with h.app.state.session_factory() as db:
        matter=Matter(user_id=h.user_a,title='重设提醒',status='active',version=1);db.add(matter);await db.flush()
        schedule=ScheduleEvent(user_id=h.user_a,matter_id=matter.matter_id,title='关联日程',starts_at=datetime.now(UTC)+timedelta(days=1),status='scheduled',source='web_native')
        todo=MatterTodo(user_id=h.user_a,matter_id=matter.matter_id,title='待办',status='open',position=0)
        db.add_all([schedule,todo]);await db.flush()
        db.add(Reminder(user_id=h.user_a,matter_id=matter.matter_id,todo_id=todo.todo_id,schedule_id=schedule.schedule_id,title='旧提醒',trigger_at=datetime.now(UTC),status='completed',acknowledged_at=datetime.now(UTC)));await db.commit()
        mid,tid,sid=matter.matter_id,todo.todo_id,schedule.schedule_id
    draft=(await h.client.post(f'/api/v1/matters/{mid}/drafts',headers=auth(h.token_a),json={'idempotency_key':'reschedule','todo_id':tid})).json()
    patch=await h.client.patch(f"/api/v1/matters/drafts/{draft['draft_id']}",headers=auth(h.token_a),json={'expected_version':draft['version'],'todos':[{'todo_id':tid,'title':'待办','reminder_at':(datetime.now(UTC)+timedelta(hours=1)).isoformat()}]})
    assert patch.status_code==200,patch.text
    result=await h.client.post(f"/api/v1/matters/drafts/{draft['draft_id']}/confirm",headers={**auth(h.token_a),'Idempotency-Key':'reschedule-confirm'},json={'expected_version':patch.json()['version'],'create_schedule':False})
    assert result.status_code==200,result.text
    async with h.app.state.session_factory() as db:
        reminder=await db.scalar(select(Reminder).where(Reminder.todo_id==tid,Reminder.status=='pending'))
        assert reminder.schedule_id==sid

@pytest.mark.asyncio
async def test_scheduler_filters_lifecycle_effective_date_and_account(harness, monkeypatch):
    from datetime import timedelta
    from assistant_api.models import CommutePlan
    from assistant_api.commute.scheduler import scheduler_tick
    from assistant_api.services import commute_lifecycle
    h=harness;now=datetime.now(UTC);called=[]
    async def materialize(db,runtime,user,pid,day):
        called.append(pid);return {'schedule_id':'test','replayed':False}
    monkeypatch.setattr(commute_lifecycle,'generate_day',materialize)
    async with h.app.state.session_factory() as db:
        expected=None
        for case in ['active','disabled','archived','deleted','future','foreign']:
            uid=h.user_b if case=='foreign' else h.user_a
            matter=Matter(user_id=uid,title=case,status=case if case in ['archived','deleted'] else 'active',version=1,deleted_at=now if case=='deleted' else None)
            db.add(matter);await db.flush()
            plan=CommutePlan(user_id=uid,matter_id=matter.matter_id,name=case,enabled=case!='disabled',timezone='UTC',weekdays_json=[1,2,3,4,5,6,7],arrival_local_time='23:59',check_local_time='01:00',transport_mode='driving',buffer_minutes=10,origin_label='test',origin_latitude=24,origin_longitude=109,destination_label='test',destination_latitude=24.1,destination_longitude=109.1,effective_from=now.date()+timedelta(days=1) if case=='future' else now.date(),revision=1)
            db.add(plan);await db.flush()
            if case=='active':expected=plan.plan_id
        await db.commit()
        await scheduler_tick(db,h.settings.model_copy(update={'commute_auto_ai_enabled':True}),owner='test-scoped',now=now,runtime=object(),enqueue_enabled=False,user_id=h.user_a)
        assert called==[expected]

@pytest.mark.asyncio
async def test_genuinely_unknown_request_remains_unknown(harness):
    h=harness
    async with h.app.state.session_factory() as db:
        user=await db.get(User,h.user_a)
        run=NexusRun(user_id=h.user_a,purpose=matter_plan.PURPOSE,status='unknown',idempotency_key='unknown',request_text='uncertain',context_json={'text':'uncertain'})
        db.add(run);await db.commit()
        result=await matters.get_intake_result(db,user,run.local_run_id)
        assert result['confirm_status']=='unknown' and result['outcome']=='unknown'
        assert result['target_lifecycle'] is None and result['history_target_id'] is None
