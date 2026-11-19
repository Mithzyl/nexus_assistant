"""Focused task-selected memory checks using isolated SQLite and fixed Nexus responses.

Run .venv/bin/python -m pytest tests/test_preferences.py. No live model, map,
business database, regression suite or hash/file fingerprint check is invoked.
Legacy control endpoints are tested only for compatibility and data preservation.
"""
from uuid import uuid4

import pytest
from sqlalchemy import select

from assistant_api.models import MatterChange, MatterDraft, ScheduleEvent, UserProfile
from conftest import auth, tool_auth
from test_task_memory import activate, callback, memory_upstream, submit


def place(crs='GCJ02'):
    """Build a confirmed place without a category or a reserved role name."""
    return {'entry_id':str(uuid4()),'version':1,'status':'active','text':'常去的工坊',
            'typed_data':{'memory_type':'place','label':'工坊','address':'样本路 1 号',
                          'latitude':31.2,'longitude':121.4,'coordinate_system':crs}}


async def configure(h, items, defaults, *, purposes=None, version=0, key='preferences-first'):
    """Bind open role names to owned memory versions through the authenticated settings API."""
    return await h.client.patch('/api/v1/preferences',headers=auth(h.token_a),json={
        'expected_version':version,'idempotency_key':key,
        'purposes':purposes or {'schedule':True,'route':True,'reminder':True},
        'bindings':{role:{'entry_id':item['entry_id'],'version':item['version']} for role,item in items.items()},
        'defaults':defaults})


async def test_preferences_owner_version_and_replay(harness):
    """Settings preserve unrelated JSON, replay safely and reject stale or foreign updates."""
    h=harness
    async with h.app.state.session_factory() as db:
        profile=await db.get(UserProfile,h.user_a)
        if profile is None:
            profile=UserProfile(user_id=h.user_a)
            db.add(profile)
        profile.preferences_json={'unrelated':{'keep':True}}
        await db.commit()
    fact=place()
    async with memory_upstream(h,[fact]):
        one=await configure(h,{'my-workshop':fact},{'schedule':{'location':'my-workshop'}})
        assert one.status_code==200,one.text
        assert one.json()['version']==1
        replay=await configure(h,{'my-workshop':fact},{'schedule':{'location':'my-workshop'}})
        assert replay.status_code==200 and replay.json()['replayed']
        stale=await configure(h,{}, {}, key='preferences-stale')
        assert stale.status_code==409
        foreign=await h.client.get('/api/v1/preferences',headers=auth(h.token_b))
        assert foreign.json()['version']==0 and foreign.json()['bindings']=={}
    async with h.app.state.session_factory() as db:
        assert (await db.get(UserProfile,h.user_a)).preferences_json['unrelated']=={'keep':True}


async def test_concurrent_owner_setting_update_cannot_overwrite_winner(harness, monkeypatch):
    """A second real SQL update during a remote read invalidates the first request's JSON comparison."""
    import asyncio
    from assistant_api.services import preferences
    h=harness; fact=place(); entered=asyncio.Event(); release=asyncio.Event()
    original=preferences.current_memory

    async def wait_for_other_update(*args,**kwargs):
        """Pause the binding reader so another authenticated update can commit first."""
        result=await original(*args,**kwargs)
        entered.set()
        await release.wait()
        return result

    async with memory_upstream(h,[fact]):
        monkeypatch.setattr(preferences,'current_memory',wait_for_other_update)
        first=asyncio.create_task(configure(h,{'工坊':fact},{'schedule':{'location':'工坊'}}))
        await entered.wait()
        winner=await h.client.patch('/api/v1/preferences',headers=auth(h.token_a),json={
            'expected_version':0,'idempotency_key':'concurrent-winner','purposes':{'route':True}})
        release.set()
        loser=await first
        assert winner.status_code==200,winner.text
        assert loser.status_code==409,loser.text
        actual=(await h.client.get('/api/v1/preferences',headers=auth(h.token_a))).json()
        assert actual['version']==1 and actual['bindings']=={} and actual['purposes']['route']


async def test_model_selected_memory_needs_no_manual_purpose_or_role(harness):
    """A task can adopt an owned retrieved fact without populating any preference configuration."""
    h=harness
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'我习惯提前十分钟收到日程提醒。'}
    async with memory_upstream(h,[fact]):
        task=await submit(h,text='创建日程：明天九点开会，按我的习惯提醒。',key='no-manual-role-task')
        row=await activate(h,task.json()['local_run_id'])
        result=await callback(h,row,'create_schedule',{
            'title':'开会','starts_at':'2030-11-04T09:00:00+08:00','idempotency_key':'no-manual-role-save',
            'memory_defaults':{'reminder_offset_seconds':{
                'entry_id':fact['entry_id'],'version':1,'source_quote':fact['text'],
                'value':-600,'reason':'本次要求按习惯提醒，原文明确提前十分钟。'}}},'no-manual-role-one')
        assert result.json()['execution_status']=='succeeded',result.text
        saved=result.json()['result']['data']
        assert saved['reminders'][0]['trigger_at']=='2030-11-04T00:50:00Z'
        source=saved['preference_application']['fields']['reminder_offset_seconds']
        assert source['source']=='memory' and source['selected_by']=='ai'
        assert source['source_quote']==fact['text'] and source['value_origin']=='model_interpretation'
        config=(await h.client.get('/api/v1/preferences',headers=auth(h.token_a))).json()
        assert config['version']==0 and config['bindings']=={}


async def test_ask_user_retains_cards_choices_and_continuation_history(harness):
    """An ambiguous memory produces a selectable question card, with no business write and choices in history."""
    import json
    from assistant_api.services.agent_responses import final_projection
    from assistant_api.services.operation_intake import message_history
    from assistant_api.models import User
    text=json.dumps({'decision':'ask_user','question':'明天去哪个工坊？',
        'choices':['城东工坊','城西工坊'],'cards':[{'kind':'info','title':'请选择地点',
        'blocks':[{'type':'text','text':'记忆里有两个工坊，本次还没有明确是哪一个。'}]}]},ensure_ascii=False)
    projection=final_projection(text,{})
    assert projection['pending_context']['cards'][0]['title']=='请选择地点'
    h=harness
    async with memory_upstream(h):
        h.fake.final_text=text
        task=await submit(h,text='明天去工坊。',key='ask-preference-place')
        assert task.json()['outcome']=='needs_clarification',task.text
        assert task.json()['pending_context']['choices']==['城东工坊','城西工坊']
        async with h.app.state.session_factory() as db:
            user=await db.get(User,h.user_a)
            history=await message_history(db,user,'第二个',task.json()['local_run_id'])
            assert history[-1]['clarification']['choices']==['城东工坊','城西工坊']


def selected(item, value, *, reason='本次输入适用这条已检索记忆。', quote=None):
    """Keep proposed business values separate from an exact source quote and revision."""
    return {'entry_id':item['entry_id'],'version':item['version'],'source_quote':quote or item['text'],
            'value':value,'reason':reason}


async def resolution(h, purpose, selections, **extra):
    """Read actual resolution output using the owner's current session."""
    return await h.client.post('/api/v1/preferences/resolve',headers=auth(h.token_a),
        json={'purpose':purpose,'selections':selections,**extra})


async def test_selected_schedule_saves_sources_and_replays_without_reapplying(harness):
    """Only chosen facts fill omitted fields, with actual provenance in the same saved transaction."""
    h=harness; location=place()
    reminder={'entry_id':str(uuid4()),'version':1,'status':'active','text':'提前十分钟提醒。',
        'app_data':{'fields':{'business_value':{'reminder_offset_seconds':-600}}}}
    async with memory_upstream(h,[location,reminder]) as (_,items):
        body={'title':'工坊碰面','starts_at':'2030-11-04T09:00:00+08:00','idempotency_key':'selected-schedule',
            'memory_defaults':{'location':selected(location,'工坊'),
                               'reminder_offset_seconds':selected(reminder,-600)}}
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        assert response.status_code==201,response.text
        data=response.json(); receipt=data['preference_application']
        assert data['schedule']['location']=='工坊' and data['schedule']['coordinate_system']=='gcj02'
        assert data['reminders'][0]['trigger_at']=='2030-11-04T00:50:00Z'
        assert receipt['application_status']=='applied' and receipt['selection_mode']=='model'
        assert receipt['fields']['location']['value_origin']=='structured_memory'
        items[0]['version']=2
        repeated=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        assert repeated.status_code==201 and repeated.json()['replayed'],repeated.text
        assert repeated.json()['preference_application']==receipt
        saved=await h.client.get('/api/v1/schedules/'+data['schedule']['schedule_id'],headers=auth(h.token_a))
        assert saved.json()['preference_application']==receipt
    async with h.app.state.session_factory() as db:
        records=list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.matter_id==data['matter']['matter_id'])))
        assert len(records)==1
        draft=await db.scalar(select(MatterDraft).where(MatterDraft.origin_key==body['idempotency_key']))
        assert draft.payload_json['analysis']['preference_application']==receipt
        assert (await db.get(MatterChange,data['change_id'])).after_json['preference_application']==receipt


async def test_explicit_null_and_existing_edit_keep_their_meaning(harness):
    """A clear and a memory selection conflict; a clear by itself remains unchanged on later edits."""
    h=harness; fact=place()
    reminder={'entry_id':str(uuid4()),'version':1,'status':'active','text':'日程提前十分钟提醒。'}
    async with memory_upstream(h,[fact,reminder]):
        body={'title':'独立会议','starts_at':'2030-11-04T09:00:00+08:00','location':None,'reminder_at':None,
            'idempotency_key':'clear-selected-default','memory_defaults':{
                'location':selected(fact,'工坊'),'reminder_offset_seconds':selected(reminder,-600)}}
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        assert response.status_code==422 and response.json()['error']['code']=='input_source_conflict',response.text
        body.pop('memory_defaults')
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        assert response.status_code==201,response.text
        data=response.json()
        assert data['schedule'].get('location') is None and data['reminders']==[]
        updated=await h.client.patch('/api/v1/schedules/'+data['schedule']['schedule_id'],headers=auth(h.token_a),json={
            'title':'只改标题','expected_matter_version':data['matter']['version'],'idempotency_key':'selected-edit-title'})
        assert updated.status_code==200,updated.text
        assert updated.json()['schedule'].get('location') is None and updated.json()['reminders']==[]


async def test_current_version_scope_coordinates_and_platform_off(harness):
    """Changed, out-of-scope, incompatible or disabled sources are unusable without widening permissions."""
    h=harness; fact=place('BD09')
    point={'latitude':31.2,'longitude':121.4,'coordinate_system':'gcj02'}
    candidate=selected(fact,point)
    async with memory_upstream(h,[fact]) as (_,items):
        result=await resolution(h,'route',{'origin':selected(fact,point)})
        assert result.json()['fields']['origin']['reason']=='coordinate_system_unsupported'
        items[0]['version']=2
        stale=await resolution(h,'route',{'origin':candidate})
        assert stale.json()['fields']['origin']['reason']=='memory_changed'
        items[0]['version']=1
        items[0]['typed_data']['coordinate_system']='GCJ02'
        items[0]['app_data']={'fields':{'applicability':{'purposes':['schedule']}}}
        scoped=await resolution(h,'route',{'origin':selected(fact,point)})
        assert scoped.json()['fields']['origin']['reason']=='not_applicable'
    async with memory_upstream(h,[fact],failure='disabled'):
        disabled=await resolution(h,'route',{'origin':selected(fact,point)})
        assert disabled.json()['fields']['origin']['reason']=='memory_disabled'


async def test_agent_semantic_activity_selection_does_not_require_equal_titles(harness):
    """The Agent selects relevance; backend checks the actual date/time instead of exact activity wording."""
    h=harness
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'星期一工坊碰面在九点。',
        'typed_data':{'memory_type':'time_preference','activity':'工坊碰面','preferred_time':'09:00','timezone':'Asia/Shanghai'},
        'app_data':{'fields':{'business_value':{'weekdays':[1]}}}}
    candidate=selected(fact,'2030-11-04T09:00:00+08:00',reason='去工坊讨论是本次要安排的碰面。')
    async with memory_upstream(h,[fact]):
        no_date=await resolution(h,'schedule',{'starts_at':candidate},activity='去工坊讨论')
        assert no_date.json()['fields']['starts_at']['reason']=='reference_date_required'
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json={
            'title':'去工坊讨论','preference_date':'2030-11-04','idempotency_key':'semantic-activity',
            'memory_defaults':{'starts_at':candidate}})
        assert response.status_code==201,response.text
        assert response.json()['schedule']['starts_at']=='2030-11-04T01:00:00Z'


async def test_route_uses_selected_endpoints_and_mode_with_explicit_override(harness):
    """A mode must have one source; explicit mode can be combined with separately selected endpoint memories."""
    h=harness; origin=place(); destination=place()
    destination['typed_data'].update(label='目的地',latitude=31.3,longitude=121.5)
    mode={'entry_id':str(uuid4()),'version':1,'status':'active','text':'平时坐公交。',
        'typed_data':{'memory_type':'commute_preference','travel_mode':'transit'}}
    defaults={'origin':selected(origin,{'latitude':31.2,'longitude':121.4,'coordinate_system':'gcj02'}),
        'destination':selected(destination,{'latitude':31.3,'longitude':121.5,'coordinate_system':'gcj02'}),
        'mode':selected(mode,'transit')}
    async with memory_upstream(h,[origin,destination,mode]):
        body={'depart_at':'2030-11-04T08:00:00+08:00','memory_defaults':defaults}
        response=await h.client.post('/api/v1/commute/estimate',headers=auth(h.token_a),json=body)
        assert response.status_code==200,response.text
        assert response.json()['mode']=='transit' and response.json()['origin_basis']=='memory'
        assert response.json()['preference_application']['fields']['mode']['source']=='memory'
        explicit=await h.client.post('/api/v1/commute/estimate',headers=auth(h.token_a),json={**body,'mode':'walking'})
        assert explicit.status_code==422 and explicit.json()['error']['code']=='input_source_conflict',explicit.text
        explicit=await h.client.post('/api/v1/commute/estimate',headers=auth(h.token_a),json={
            **body,'mode':'walking','memory_defaults':{name:value for name,value in defaults.items() if name!='mode'}})
        assert explicit.status_code==200,explicit.text
        assert explicit.json()['mode']=='walking'


async def test_future_date_and_weekday_restrictions_are_not_ignored(harness):
    """A currently active memory cannot supply a value outside its declared future validity or weekdays."""
    h=harness
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'星期一坐公交。',
        'typed_data':{'memory_type':'commute_preference','travel_mode':'transit','weekdays':[1]},
        'app_data':{'fields':{'applicability':{'effective_until':'2029-11-04'}}}}
    async with memory_upstream(h,[fact]) as (_,items):
        outside=await resolution(h,'route',{'mode':selected(fact,'transit')},reference_date='2030-11-04')
        assert outside.json()['fields']['mode']['reason']=='not_applicable'
        items[0].pop('app_data')
        weekday=await resolution(h,'route',{'mode':selected(fact,'transit')},reference_date='2030-11-05')
        assert weekday.json()['fields']['mode']['reason']=='weekday_not_applicable'


@pytest.mark.parametrize('change',['memory','platform','legacy_controls'])
async def test_confirmation_rechecks_used_sources_not_legacy_role_controls(harness, monkeypatch, change):
    """Actual source changes block both confirmation paths; obsolete local role controls do not gate new tasks."""
    from assistant_api.services import matters
    h=harness; fact=place(); original=matters.create_structured_draft
    async with memory_upstream(h,[fact]) as (_,items):
        async def changed_after_preparation(*args,**kwargs):
            """Change one source after the preview exists and before formal confirmation."""
            result=await original(*args,**kwargs)
            if change=='memory':
                items[0]['version']=2
            elif change=='platform':
                async def memory_disabled(*args,**kwargs):
                    """Represent the owner switching off the existing platform memory permission."""
                    return {'enabled':False}
                monkeypatch.setattr(h.app.state.nexus_client,'application_memory_settings',memory_disabled)
            else:
                updated=await configure(h,{}, {},purposes={'schedule':False},key='legacy-off-no-gate')
                assert updated.status_code==200,updated.text
            return result
        monkeypatch.setattr(matters,'create_structured_draft',changed_after_preparation)
        body={'title':'保存前核对','starts_at':'2030-11-04T09:00:00+08:00','idempotency_key':'selected-before-save',
              'memory_defaults':{'location':selected(fact,'工坊')}}
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        expected=201 if change=='legacy_controls' else 409
        assert response.status_code==expected,response.text
        if expected==409:
            async with h.app.state.session_factory() as db:
                assert not list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.title==body['title'])))
                draft=await db.scalar(select(MatterDraft).where(MatterDraft.origin_key==body['idempotency_key']))
            generic=await h.client.post('/api/v1/matters/drafts/'+draft.draft_id+'/confirm',
                headers={**auth(h.token_a),'Idempotency-Key':'generic-selected-confirm'},json={'expected_version':draft.version})
            assert generic.status_code==409,generic.text


async def test_unselected_retrieved_memory_does_not_block_the_selected_source(harness):
    """Only sources actually adopted by this operation affect its dependency check."""
    h=harness; chosen=place()
    unused={'entry_id':str(uuid4()),'version':1,'status':'active','text':'我习惯提前十分钟提醒。'}
    async with memory_upstream(h,[chosen,unused]) as (_,items):
        task=await submit(h,text='创建工坊日程，顺便考虑提醒习惯。',key='unused-memory-task')
        row=await activate(h,task.json()['local_run_id'])
        assert len(row.context_json['api_flow']['memory_sources'])==2
        items[1]['version']=2
        response=await callback(h,row,'create_schedule',{'title':'工坊碰面','starts_at':'2030-11-04T09:00:00+08:00',
            'idempotency_key':'chosen-only','memory_defaults':{'location':selected(chosen,'工坊')}},'chosen-memory-only')
        assert response.json()['execution_status']=='succeeded',response.text


async def test_unretrieved_source_is_known_failure_then_same_agent_can_ask(harness):
    """A guessed source cannot be used, and the original Agent remains able to ask for a selection."""
    import json
    from assistant_api.models import NexusRun
    h=harness; fact=place()
    async with memory_upstream(h,[fact]):
        task=await submit(h,text='创建工坊日程。',key='unretrieved-source-task')
        row=await activate(h,task.json()['local_run_id'])
        async with h.app.state.session_factory() as db:
            saved=await db.get(NexusRun,row.local_run_id)
            saved.context_json={**saved.context_json,'api_flow':{**saved.context_json['api_flow'],'memory_sources':[]}}
            await db.commit()
        response=await callback(h,row,'create_schedule',{'title':'尚未选择','starts_at':'2030-11-04T09:00:00+08:00',
            'idempotency_key':'not-retrieved','memory_defaults':{'location':selected(fact,'工坊')}},'unretrieved-first')
        assert response.json()['execution_status']=='failed',response.text
        assert response.json()['error']['code']=='preference_selection_unresolved'
        async with h.app.state.session_factory() as db:
            assert not list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.title=='尚未选择')))
        question=await h.client.post('/internal/nexus/agent-responses',headers=tool_auth(h.settings),json={
            'protocol_version':'1','application_id':'personal_assistant','external_user_id':h.user_a,
            'run_id':row.nexus_run_id,'workflow_id':row.workflow_execution_id,'response_id':'ask-after-selection',
            'execution_id':'ask-after-selection','content':json.dumps({'decision':'ask_user',
                'question':'请选择地点。','choices':['工坊','另一个地点']},ensure_ascii=False)})
        assert question.json()['disposition']=='complete',question.text
        assert h.fake.run_seq==1


async def test_text_memory_cannot_supply_invented_coordinates_or_false_quote(harness):
    """A textual preference may be interpreted, but it cannot prove a fabricated source quote or coordinates."""
    h=harness
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'我常去城东工坊。'}
    async with memory_upstream(h,[fact]):
        false_quote=await resolution(h,'schedule',{'location':selected(fact,'工坊',quote='不存在的原话')})
        assert false_quote.json()['fields']['location']['reason']=='source_quote_mismatch'
        invented=await resolution(h,'route',{'origin':selected(fact,{
            'latitude':31.2,'longitude':121.4,'coordinate_system':'gcj02'})})
        assert invented.json()['fields']['origin']['reason']=='verified_coordinates_required'


async def test_selected_value_cannot_contradict_confirmed_structured_fact(harness):
    """Backend verifies known structured values instead of claiming arbitrary model interpretations are confirmed."""
    h=harness
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'通常坐公交。',
        'typed_data':{'memory_type':'commute_preference','travel_mode':'transit'}}
    async with memory_upstream(h,[fact]):
        result=await resolution(h,'route',{'mode':selected(fact,'driving')})
        assert result.json()['fields']['mode']['reason']=='structured_value_mismatch'


async def test_legacy_config_does_not_autofill_and_opt_out_is_preserved(harness):
    """Old stored settings stay intact but do not silently select memories for a new request."""
    h=harness; fact=place()
    async with memory_upstream(h,[fact]):
        await configure(h,{'old-role':fact},{'schedule':{'location':'old-role'}})
        body={'title':'无记忆选择','starts_at':'2030-11-04T09:00:00+08:00','idempotency_key':'no-auto-role'}
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        assert response.status_code==201,response.text
        assert response.json()['schedule'].get('location') is None
        disabled=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json={
            **body,'idempotency_key':'memory-opt-out','use_preferences':False,'memory_defaults':{'location':selected(fact,'工坊')}})
        assert disabled.status_code==422 and disabled.json()['error']['code']=='input_source_conflict',disabled.text


@pytest.mark.parametrize('variation',[{'location':None},{'use_preferences':False}])
async def test_replay_keeps_omission_clear_and_opt_out_distinct(harness, variation):
    """A changed explicit value or opt-out cannot reuse the original memory-dependent request key."""
    h=harness; fact=place()
    async with memory_upstream(h,[fact]):
        body={'title':'重放含义','starts_at':'2030-11-04T09:00:00+08:00','idempotency_key':'memory-replay',
              'memory_defaults':{'location':selected(fact,'工坊')}}
        first=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json=body)
        assert first.status_code==201,first.text
        changed=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json={**body,**variation})
        assert changed.status_code==409,changed.text


async def test_changed_preview_cannot_save_inaccurate_attribution(harness, monkeypatch):
    """A preview editor cannot change adopted fields while keeping the old actual-source claim."""
    from assistant_api.services import matters
    h=harness; fact=place(); original=matters.create_structured_draft
    async def alter_preview(*args,**kwargs):
        """Change the preview after selection while retaining its saved source metadata."""
        result=await original(*args,**kwargs); db=args[0]
        draft=await db.get(MatterDraft,result['draft_id'])
        draft.payload_json={**draft.payload_json,'schedule':{**draft.payload_json['schedule'],'location':'另一个场所'}}
        await db.commit()
        return matters._serialize_draft(draft)
    async with memory_upstream(h,[fact]):
        monkeypatch.setattr(matters,'create_structured_draft',alter_preview)
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token_a),json={
            'title':'来源一致性','starts_at':'2030-11-04T09:00:00+08:00','idempotency_key':'preview-selection',
            'memory_defaults':{'location':selected(fact,'工坊')}})
        assert response.status_code==409,response.text
        assert response.json()['error']['code']=='preferences_draft_changed'


async def test_only_resolution_is_in_the_agent_tool_catalog(harness):
    """The Agent can inspect proposed uses, but cannot edit legacy controls or memory permissions."""
    from assistant_api.services.api_operations import OperationCatalog
    catalog=OperationCatalog(harness.app)
    assert catalog.operations['resolve_business_preferences'].effect=='read'
    assert not any(op.path=='/api/v1/preferences' for op in catalog.operations.values())
