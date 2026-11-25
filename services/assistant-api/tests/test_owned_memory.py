"""Targeted owned-memory acceptance; isolated SQLite, no live model or provider calls.

Run .venv/bin/python -m pytest tests/test_owned_memory.py. Live Agent verification
is separately recorded; these checks exercise storage and business dispatch.
"""
import asyncio
import base64
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from assistant_api.main import create_app
from assistant_api.core.config import Settings
from assistant_api.models import NexusRun, User
from conftest import seed_user, login, auth


@pytest_asyncio.fixture
async def owned(tmp_path):
    """Create two real local accounts with a private key and reject every Nexus HTTP request."""
    key = tmp_path / 'memory.key'
    key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    calls = []

    async def unavailable(request):
        """Record accidental remote dependencies and simulate a disconnected Nexus."""
        calls.append(str(request.url))
        return httpx.Response(503)

    settings = Settings(_env_file=None, environment='test', allow_schema_create=True,
        database_url=f'sqlite+aiosqlite:///{tmp_path}/memory.db', memory_encryption_key_path=key)
    app = create_app(settings, nexus_transport=httpx.MockTransport(unavailable))
    async with app.router.lifespan_context(app):
        a = await seed_user(app, email='memory-a@example.com', display_name='A')
        b = await seed_user(app, email='memory-b@example.com', display_name='B')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://assistant.test') as client:
            yield SimpleNamespace(app=app, client=client, a=a, b=b, calls=calls, path=tmp_path,
                token=await login(client, 'memory-a@example.com'), other=await login(client, 'memory-b@example.com'))


async def command(h, **fields):
    """Submit one owner command without assuming a prior Nexus conversation."""
    return await h.client.post('/api/v1/memory/commands', headers=auth(h.token),
        json={'key':str(uuid4()), **fields})


async def test_local_crud_versions_owner_and_ciphertext(owned):
    """CRUD works without Nexus, preserves source/history, and rejects stale or foreign mutations."""
    h = owned
    first = await command(h, action='save', text='工作日通勤我偏好骑行。')
    assert first.status_code == 200, first.text
    entry = first.json()['entry_id']
    page = await h.client.get('/api/v1/memory', headers=auth(h.token))
    assert page.json()['items'][0]['entry_version'] == 1
    assert page.json()['items'][0]['source']['text_origin'] == 'user_quote'
    foreign = await h.client.get(f'/api/v1/memory/history/{entry}', headers=auth(h.other))
    assert foreign.status_code == 404
    changed = await command(h, action='correct', entry_id=entry, expected_version=1, text='通勤默认公交。')
    assert changed.status_code == 200 and changed.json()['version'] == 2
    stale = await command(h, action='forget', entry_id=entry, expected_version=1)
    assert stale.status_code == 409
    forgotten = await command(h, action='forget', entry_id=entry, expected_version=2)
    assert forgotten.json()['version'] == 3
    recalled = await h.client.post('/api/v1/business-memory/query', headers=auth(h.token), json={'mode':'browse'})
    assert recalled.status_code == 200 and recalled.json()['items'] == []
    restored = await command(h, action='undo', entry_id=entry, expected_version=3)
    assert restored.json()['version'] == 4
    history = (await h.client.get(f'/api/v1/memory/history/{entry}', headers=auth(h.token))).json()
    assert [item['version'] for item in history['items']] == [4,3,2,1]
    assert history['items'][-1]['text'] == '工作日通勤我偏好骑行。'
    assert not h.calls
    assert '工作日通勤我偏好骑行。'.encode() not in (h.path/'memory.db').read_bytes()


async def test_replay_parameter_conflict_and_concurrent_version(owned):
    """The same request writes once, changed parameters fail, and only one revision update wins."""
    h = owned
    body={'action':'save','key':'same-save','text':'常用地点是城东工坊。'}
    one=await h.client.post('/api/v1/memory/commands',headers=auth(h.token),json=body)
    two=await h.client.post('/api/v1/memory/commands',headers=auth(h.token),json=body)
    assert one.status_code==two.status_code==200
    assert one.json()['entry_id']==two.json()['entry_id']
    conflict=await h.client.post('/api/v1/memory/commands',headers=auth(h.token),json={**body,'text':'不同内容'})
    assert conflict.status_code==409
    entry=one.json()['entry_id']
    replies=await asyncio.gather(*(command(h,action='correct',entry_id=entry,expected_version=1,text=text)
        for text in ['常用地点是东工坊。','常用地点是西工坊。']))
    assert sorted(reply.status_code for reply in replies)==[200,409]
    assert not h.calls


async def test_agent_source_deferred_and_cross_business_recall(owned):
    """Only current user quotes authorize mutations; after-answer work persists and runs without another model."""
    from assistant_api.services import memory_repository
    h=owned
    run_id=str(uuid4())
    async with h.app.state.session_factory() as db:
        db.add(NexusRun(local_run_id=run_id,user_id=h.a,nexus_run_id=str(uuid4()),
            idempotency_key='memory-agent',request_text='记住：通勤默认公交，事项出行也采用它。',
            status='running',context_json={'api_flow':{'text':'记住：通勤默认公交，事项出行也采用它。','trigger_source':'chat'}}))
        await db.commit()
    async with h.app.state.session_factory() as db:
        store=h.app.state.memory_store
        result=await memory_repository.agent_command(db,store,await db.get(User,h.a),run_id,{
            'action':'save','source_quote':'通勤默认公交，事项出行也采用它。',
            'text':'通勤默认公交，事项出行也采用它。','intent':'user_requested',
            'execution':'after_answer'},key='queued-source')
        assert result['status']=='queued'
    assert await memory_repository.process_pending(h.app)==0
    async with h.app.state.session_factory() as db:
        root=await db.get(NexusRun,run_id)
        root.status='succeeded'
        root.context_json={**root.context_json,'caller_processed':True}
        root.result_json={'final_text':json.dumps({'decision':'answer','answer':'已加入待处理队列。'})}
        await db.commit()
    await memory_repository.release_after_answer(h.app,run_id)
    assert await memory_repository.process_pending(h.app)==1
    for purpose in ['通勤','事项出行']:
        response=await h.client.post('/api/v1/business-memory/query',headers=auth(h.token),
            json={'query':'公交','purpose':purpose})
        assert len(response.json()['items'])==1
        fact=response.json()['items'][0]
        assert fact['source']['kind']=='assistant_memory'
    async with h.app.state.session_factory() as db:
        root=await db.get(NexusRun,run_id)
        root.status='running'
        root.context_json={'api_flow':{'text':'把上面的出行习惯改成骑行。','trigger_source':'chat'}}
        await db.commit()
        result=await memory_repository.agent_command(db,store,await db.get(User,h.a),run_id,{
            'action':'correct','source_quote':'把上面的出行习惯改成骑行。','text':'通勤和事项出行默认骑行。',
            'entry_id':fact['entry_id'],'expected_entry_version':fact['entry_version'],
            'intent':'user_requested','execution':'before_answer'},key='cross-business-correct')
        assert result['entry_version']==2
    bad=await h.client.post('/api/v1/memory/commands',headers=auth(h.token),json={
        'action':'save','text':'越权','key':'bad','user_id':h.b})
    assert bad.status_code==422
    assert not h.calls


async def test_mixed_digit_chinese_recall_and_disabled_management(owned):
    """Mixed numeral/CJK input still recalls places; disabled auto-use does not disable owner CRUD."""
    h=owned
    await command(h,action='save',text='我常去城东工坊讨论。')
    result=await h.client.post('/api/v1/business-memory/query',headers=auth(h.token),
        json={'query':'明天上午9点去工坊讨论'})
    assert len(result.json()['items'])==1
    settings=(await h.client.get('/api/v1/memory/settings',headers=auth(h.token))).json()
    off=await command(h,action='settings',expected_version=settings['version'],enabled=False)
    assert off.status_code==200
    result=await h.client.post('/api/v1/business-memory/query',headers=auth(h.token),json={'mode':'browse'})
    assert result.json()['status']=='disabled'
    assert (await command(h,action='save',text='关闭自动应用后仍可本人保存。')).status_code==200
    assert not h.calls


async def test_model_body_dispatches_shared_memory_and_schedule(owned):
    """The real callback parser dispatches local CRUD and source-checked schedule creation using actual API definitions."""
    from assistant_api.services.agent_responses import process_response
    from assistant_api.services.api_operations import OperationCatalog
    from assistant_api.models import ScheduleEvent, Reminder
    h=owned
    run_id=str(uuid4()); remote=str(uuid4()); workflow=str(uuid4())
    catalog=OperationCatalog(h.app)
    operations=['mutate_business_memory','recall_business_memory','create_schedule']
    async with h.app.state.session_factory() as db:
        root=NexusRun(local_run_id=run_id,user_id=h.a,nexus_run_id=remote,workflow_execution_id=workflow,
            idempotency_key='callback-test',request_text='记住：通勤与事项都提前12分钟提醒。然后安排讨论。',
            status='running',context_json={'caller_processed':True,'api_flow':{
                'text':'记住：通勤与事项都提前12分钟提醒。然后安排讨论。','trigger_source':'chat',
                'offered_operations':operations,'receipts':[],'results':{},'visible_result':None,'step':0}})
        db.add(root);await db.commit()

    async def call(operation, body, execution):
        """Send unmodified decision JSON into the same callback implementation used by Nexus."""
        content=json.dumps({'decision':'call','call':{
            'operation_id':operation,'body':body,'summary':'当前用户授权的记忆与日程操作'}},ensure_ascii=False)
        if execution=='callback-save':
            content='```json\n'+content+'\n```'
        async with h.app.state.session_factory() as db:
            return await process_response(db,h.app,{'protocol_version':'1','application_id':'personal_assistant',
                'external_user_id':h.a,'run_id':remote,'workflow_id':workflow,'response_id':execution,
                'execution_id':execution,'content':content})

    invalid=await call('mutate_business_memory',{'action':'correct','source_quote':'通勤与事项都提前12分钟提醒。',
        'entry_id':str(uuid4()),'expected_entry_version':1},'callback-invalid')
    assert invalid['execution_status']=='failed' and invalid['disposition']=='continue',invalid
    assert invalid['result'] is None
    created=await call('mutate_business_memory',{'action':'save','source_quote':'通勤与事项都提前12分钟提醒。'},'callback-save')
    assert created['execution_status']=='succeeded',created
    saved=created['result']['data']
    recalled=await call('recall_business_memory',{'query':'提醒','purpose':'事项'},'callback-read')
    assert recalled['result']['data']['items'][0]['entry_id']==saved['entry_id']
    invalid_source=await call('create_schedule',{'title':'讨论','starts_at':'2030-11-10T09:00:00+08:00',
        'idempotency_key':'bad-quote','memory_defaults':{'reminder_offset_seconds':{
            'entry_id':saved['entry_id'],'version':saved['entry_version'],'source_quote':'不是原文',
            'value':-720,'reason':'检查未采用来源的失败响应。'}}},'callback-bad-quote')
    assert invalid_source['execution_status']=='failed' and invalid_source['result'] is None,invalid_source
    duplicate_source=await call('create_schedule',{'title':'讨论','starts_at':'2030-11-10T09:00:00+08:00',
        'idempotency_key':'duplicate-source','reminder_offset_seconds':-720,
        'memory_defaults':{'reminder_offset_seconds':{
            'entry_id':saved['entry_id'],'version':saved['entry_version'],'source_quote':'通勤与事项都提前12分钟提醒。',
            'value':-720,'reason':'重复来源应在写入前拒绝。'}}},'callback-duplicate-source')
    assert duplicate_source['execution_status']=='failed' and duplicate_source['disposition']=='continue',duplicate_source
    async with h.app.state.session_factory() as db:
        assert list(await db.scalars(select(ScheduleEvent))) == []
    scheduled=await call('create_schedule',{'title':'讨论','starts_at':'2030-11-10T09:00:00+08:00',
        'idempotency_key':'model-key','memory_defaults':{'reminder_offset_seconds':{
            'entry_id':saved['entry_id'],'version':saved['entry_version'],'source_quote':'通勤与事项都提前12分钟提醒。',
            'value':-720,'reason':'本次安排按已保存提醒习惯。'}}},'callback-schedule')
    assert scheduled['execution_status']=='succeeded',scheduled
    result=scheduled['result']['data']
    assert result['preference_application']['fields']['reminder_offset_seconds']['selected_by']=='ai'
    assert result['reminders'][0]['trigger_at']=='2030-11-10T00:48:00Z'
    assert result['preference_application']['fields']['reminder_offset_seconds']['source']=='memory'
    replayed=await call('create_schedule',{'title':'讨论','starts_at':'2030-11-10T09:00:00+08:00',
        'idempotency_key':'model-key','memory_defaults':{'reminder_offset_seconds':{
            'entry_id':saved['entry_id'],'version':saved['entry_version'],'source_quote':'通勤与事项都提前12分钟提醒。',
            'value':-720,'reason':'本次安排按已保存提醒习惯。'}}},'callback-schedule')
    assert replayed==scheduled
    async with h.app.state.session_factory() as db:
        assert len(list(await db.scalars(select(ScheduleEvent)))) == 1
        assert len(list(await db.scalars(select(Reminder)))) == 1
    assert not h.calls
    schema=h.app.openapi()['components']['schemas']['AgentMemoryCommand']['properties']
    assert 'expected_entry_version' in schema and 'expected_version' not in schema and 'user_id' not in schema
    from assistant_api.services.run_service import workflow_request
    configured=workflow_request(h.app.state.settings,'memory-loop-budget',{
        'application_response_mode':'caller_processed','wall_time_limit_ms':600000})
    assert configured.wall_time_limit_ms == 600000
    assert {'role_bindings', 'reasoning'}.isdisjoint(configured.model_dump())
    from assistant_api.services.operation_intake import model_context
    discovery={'query':'记忆 保存 纠正','text':'通勤习惯','timezone':'Asia/Shanghai',
        'now':'2030-11-10T08:00:00+08:00','history':[],'receipts':[],'step':0}
    model_context(discovery,catalog)
    first=set(discovery['offered_operations'])
    discovery['query']='create_schedule 日程创建'
    model_context(discovery,catalog)
    assert first.issubset(discovery['offered_operations'])


async def test_memory_input_sources_conflict_before_writes(owned):
    """Reject dual sources, including explicit null and disabled memory, without choosing intent or writing records."""
    from assistant_api.models import ScheduleEvent, MatterDraft
    h=owned
    item=(await command(h,action='save',text='日程提前12分钟提醒。')).json()
    selection={'entry_id':item['entry_id'],'version':1,'source_quote':'日程提前12分钟提醒。',
        'value':-720,'reason':'用户要求采用提醒习惯。'}
    base={'title':'讨论','starts_at':'2030-11-10T09:00:00+08:00',
        'memory_defaults':{'reminder_offset_seconds':selection}}
    for index,fields in enumerate([{'reminder_offset_seconds':-720},{'reminder_offset_seconds':-300},
                                  {'reminder_at':None},{'use_preferences':False}]):
        response=await h.client.post('/api/v1/schedules',headers=auth(h.token),
            json={**base,**fields,'idempotency_key':f'conflicting-sources-{index}'})
        assert response.status_code==422,response.text
        assert response.json()['error']['code']=='input_source_conflict',response.text
    resolved=await h.client.post('/api/v1/preferences/resolve',headers=auth(h.token),json={
        'purpose':'schedule','values':{'reminder_offset_seconds':-720},
        'selections':{'reminder_offset_seconds':selection}})
    assert resolved.status_code==422 and resolved.json()['error']['code']=='input_source_conflict'
    point={'latitude':31.2,'longitude':121.4,'coordinate_system':'gcj02'}
    route=await h.client.post('/api/v1/commute/estimate',headers=auth(h.token),json={
        'origin':point,'destination':point,'depart_at':'2030-11-10T08:00:00+08:00','mode':'walking',
        'memory_defaults':{'mode':{**selection,'value':'walking'}}})
    assert route.status_code==422 and route.json()['error']['code']=='input_source_conflict',route.text
    async with h.app.state.session_factory() as db:
        assert list(await db.scalars(select(ScheduleEvent)))==[]
        assert list(await db.scalars(select(MatterDraft)))==[]
    explicit=await h.client.post('/api/v1/schedules',headers=auth(h.token),json={
        'title':'明确提醒','starts_at':'2030-11-10T09:00:00+08:00',
        'reminder_offset_seconds':-300,'idempotency_key':'explicit-only-reminder'})
    assert explicit.status_code==201,explicit.text
    assert explicit.json()['reminders'][0]['trigger_at']=='2030-11-10T00:55:00Z'
    assert explicit.json()['matter']['latest_progress']=='创建日程：明确提醒'
    history=(await h.client.get('/api/v1/memory/history/'+item['entry_id'],headers=auth(h.token))).json()
    assert history['items'][0]['created_at']
    assert history['items'][0]['source']['text_origin']=='user_quote'
    assert not h.calls


async def test_deferred_conflict_keeps_newer_revision_and_reports_failure(owned):
    """A durable queued correction cannot overwrite a later owner change, even after worker restart."""
    from assistant_api.services import memory_repository
    from assistant_api.models import MemoryOperation
    h=owned
    saved=(await command(h,action='save',text='通勤乘公交。')).json()
    run_id=str(uuid4())
    async with h.app.state.session_factory() as db:
        db.add(NexusRun(local_run_id=run_id,user_id=h.a,status='running',idempotency_key='deferred-conflict',
            request_text='把通勤改成步行。',context_json={'caller_processed':True,'api_flow':{
                'text':'把通勤改成步行。','trigger_source':'chat',
                'memory_sources':[{'entry_id':saved['entry_id'],'version':1}]}}))
        await db.commit()
        queued=await memory_repository.agent_command(db,h.app.state.memory_store,await db.get(User,h.a),run_id,{
            'action':'correct','entry_id':saved['entry_id'],'expected_entry_version':1,
            'source_quote':'把通勤改成步行。','text':'通勤步行。','execution':'after_answer'},key='deferred-conflict')
    await command(h,action='correct',entry_id=saved['entry_id'],expected_version=1,text='通勤骑行。')
    async with h.app.state.session_factory() as db:
        root=await db.get(NexusRun,run_id)
        root.status='succeeded';root.result_json={'final_text':json.dumps({'decision':'answer','answer':'变更已入队。'})}
        await db.commit()
    # A new object uses only persisted queue state, not the previous process's memory.
    h.app.state.memory_store=memory_repository.MemoryStore(h.app.state.session_factory,h.path/'memory.key')
    assert await memory_repository.process_pending(h.app)==1
    result=(await h.client.get('/api/v1/memory/operations/'+queued['operation_id'],headers=auth(h.token))).json()
    assert result['status']=='failed' and result['error_code']=='memory_version_conflict'
    page=(await h.client.get('/api/v1/memory',headers=auth(h.token))).json()
    assert page['items'][0]['text']=='通勤骑行。' and page['items'][0]['version']==2


async def test_missing_key_and_invalid_source_fail_without_writes(owned):
    """Unavailable encryption and fabricated quotes never produce an apparent saved or empty success."""
    from assistant_api.services.memory_repository import agent_command
    from assistant_api.core.errors import ForbiddenError
    h=owned
    await command(h,action='save',text='原有记忆不能因密钥不可用丢失。')
    (h.path/'memory.key').unlink()
    result=await h.client.get('/api/v1/memory',headers=auth(h.token))
    assert result.status_code==503 and result.json()['error']['code']=='memory_key_unavailable'
    with pytest.raises(ForbiddenError):
        async with h.app.state.session_factory() as db:
            await agent_command(db,h.app.state.memory_store,await db.get(User,h.a),str(uuid4()),
                {'action':'save','source_quote':'不是用户的话','text':'不应保存'},key='bad-source')
    assert not h.calls


async def test_additive_migration_and_import_preserve_account_and_history(tmp_path):
    """0028 adds only missing memory storage; import retains original IDs, versions, status, source and settings."""
    from scripts.migrate_sqlite import upgrade
    from assistant_api.infrastructure.database import create_engine_and_factory
    from assistant_api.models import Base, MemoryEntry
    from assistant_api.services.memory_repository import MemoryStore
    from assistant_api.core.errors import ConflictError
    from sqlalchemy import text
    path=tmp_path/'previous.db';url=f'sqlite+aiosqlite:///{path}'
    engine,factory=create_engine_and_factory(url)
    async with engine.begin() as conn:
        await conn.run_sync(lambda connection:Base.metadata.create_all(connection,
            tables=[table for table in Base.metadata.sorted_tables if not table.name.startswith('assistant_memory_')]))
    user_id=str(uuid4())
    async with factory() as db:
        db.add(User(user_id=user_id,email='preserve@example.com',display_name='保留账号',password_verifier='original-verifier'))
        await db.commit()
    await upgrade(url)
    key=tmp_path/'memory.key';key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    store=MemoryStore(factory,key)
    entry_id=str(uuid4())
    history=[{'entry_id':entry_id,'version':version,'status':'active','text':value,
        'app_data':{'namespace':'personal_assistant','schema_version':1,'fields':{
            'source':{'kind':'user_message','quote':'原始原话'}}}} for version,value in [(1,'公交'),(2,'步行')]]
    result=await store.import_account(user_id,[{'entry':history[-1],'versions':history}],{'enabled':False,'version':5})
    assert result=={'entries':1,'versions':2}
    imported=await store.list_application_memory(user_id,history_entry_id=entry_id)
    assert [item['version'] for item in imported['items']]==[2,1]
    assert imported['items'][0]['source']['original_source']['quote']=='原始原话'
    assert (await store.application_memory_settings(user_id))['enabled'] is False
    with pytest.raises(ConflictError):
        await store.import_account(user_id,[],{'enabled':True,'version':0})
    async with factory() as db:
        assert (await db.get(User,user_id)).password_verifier=='original-verifier'
        assert await db.scalar(text("SELECT version FROM schema_migrations WHERE version='0028_owned_memory'"))
    await engine.dispose()
