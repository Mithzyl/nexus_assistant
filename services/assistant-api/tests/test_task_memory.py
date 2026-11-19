"""Focused task-start memory checks: real Assistant routes, isolated SQLite, fixed Nexus HTTP.

No real model, Nexus service, maps, regression suite or hash check is invoked.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from assistant_api.models import NexusRun, User
from assistant_api.services.execution_context import ExecutionContext
from conftest import auth, tool_auth


@asynccontextmanager
async def memory_upstream(h, items=(), *, failure=None, entered=None, release=None):
    """Fix only remote memory data and observe ordering relative to the existing fake Agent API."""
    events, values = [], list(items)
    client = h.app.state.nexus_client
    original = client._client
    h.fake.final_text = json.dumps({'decision':'answer','answer':'已按本次输入处理。'},ensure_ascii=False)

    async def handler(request):
        """Serve owner-scoped settings/pages and retain actual outgoing task payloads."""
        events.append({'method':request.method,'path':request.url.path,
                       'user':request.headers.get('x-external-user-id'),
                       'body':json.loads(request.content) if request.content else None})
        if request.url.path.endswith('/memory-settings'):
            if entered:
                entered.set()
            if release:
                await release.wait()
            if failure=='timeout':
                await asyncio.sleep(1)
            if failure=='unavailable':
                return httpx.Response(503,json={'detail':{'code':'memory_storage_unavailable'}})
            return httpx.Response(200,json={'enabled':failure!='disabled','version':1})
        if request.url.path.endswith('/me/user-memories'):
            if request.method=='POST':
                body=json.loads(request.content)
                saved={'entry_id':str(uuid4()),'version':1,'status':'active','text':body['text'],'app_data':body.get('app_data')}
                values.append(saved)
                return httpx.Response(200,json=saved)
            if request.url.params.get('history_entry_id'):
                entry=next(value for value in values if value['entry_id']==request.url.params['history_entry_id'])
                return httpx.Response(200,json={'entry_id':entry['entry_id'],'current_version':entry['version'],
                    'items':[entry],'next_offset':None})
            offset,limit=int(request.url.params.get('offset',0)),int(request.url.params.get('limit',20))
            return httpx.Response(200,json={'items':values[offset:offset+limit],
                'next_offset':offset+limit if offset+limit<len(values) else None})
        return await h.fake.handler(request)

    client._client=httpx.AsyncClient(base_url='http://nexus.test',transport=httpx.MockTransport(handler))
    try:
        yield events,values
    finally:
        await client._client.aclose()
        client._client=original


async def submit(h, text='明天带母亲去医院，给我出行建议。', key='task-memory-start'):
    """Enter through the actual authenticated natural-language task endpoint."""
    return await h.client.post('/api/v1/matters/intake',headers=auth(h.token_a),
        json={'text':text,'idempotency_key':key})


async def stored_run(h, identity):
    """Read the saved task snapshot without calling any remote endpoint."""
    async with h.app.state.session_factory() as db:
        return await db.get(NexusRun,identity)


async def activate(h, identity):
    """Keep a fixed test Agent active to exercise subsequent authenticated callbacks."""
    async with h.app.state.session_factory() as db:
        row=await db.get(NexusRun,identity)
        row.status='running'
        await db.commit()
        return row


async def callback(h, row, operation, body, execution):
    """Deliver fixed model output to the real callback for the same owned Agent."""
    return await h.client.post('/internal/nexus/agent-responses',headers=tool_auth(h.settings),json={
        'protocol_version':'1','application_id':'personal_assistant','external_user_id':h.user_a,
        'run_id':row.nexus_run_id,'workflow_id':row.workflow_execution_id,'response_id':execution,
        'execution_id':execution,'content':json.dumps({'decision':'call','call':{
            'operation_id':operation,'body':body,'summary':'处理当前任务记忆'}},ensure_ascii=False)})


async def test_task_prefetch_precedes_first_model_input_and_preserves_sources(harness):
    """A model that never chooses a memory tool still receives current owned candidates on its first call."""
    h=harness
    fact={'entry_id':str(uuid4()),'version':2,'status':'active','text':'母亲不喜欢坐地铁，出门尽量少走路'}
    async with memory_upstream(h,[fact]) as (events,_):
        response=await submit(h)
        assert response.status_code==200,response.text
        run=await stored_run(h,response.json()['local_run_id'])
        creation=next(event for event in events if event['path']=='/api/v1/app/runs')
        payload=json.loads(creation['body']['user_request'])
        memory=payload['memory_context']
        assert memory['status']=='completed'
        assert memory['result']['items'][0]['text']==fact['text']
        assert run.context_json['api_flow']['memory_sources'][0]['version']==2
        paths=[event['path'] for event in events]
        assert paths.index('/api/v1/app/me/user-memories')<paths.index('/api/v1/app/runs')
        assert all(event['user']==h.user_a for event in events)
        assert h.fake.run_seq==1 and h.fake.workflow_seq==1


async def test_confirmed_empty_is_explicit_and_same_task_queries_do_not_hit_nexus_again(harness):
    """An empty complete snapshot is reusable without telling the model to search the empty account again."""
    h=harness
    async with memory_upstream(h) as (events,_):
        result=await submit(h)
        row=await activate(h,result.json()['local_run_id'])
        snapshot=row.context_json['api_flow']['memory_prefetch']
        assert snapshot['confirmed_empty'] is True
        assert snapshot['result']['next_steps']==[]
        reads=len([event for event in events if '/me/' in event['path']])
        for number in range(2):
            response=await callback(h,row,'recall_business_memory',{'query':'另一种出行表述'},'empty-'+str(number))
            assert response.json()['execution_status']=='succeeded',response.text
            data=response.json()['result']['data']
            assert data['items']==[] and data['cache']['scope']=='current_task'
        assert len([event for event in events if '/me/' in event['path']])==reads


async def test_no_match_or_partial_scan_is_not_treated_as_an_empty_account(harness):
    """No lexical match before a scan limit retains continuation and permits actual supplemental reads."""
    h=harness
    h.app.state.settings.memory_prefetch_scan_limit=2
    entries=[{'entry_id':str(uuid4()),'version':1,'status':'active','text':'望远镜需要先校准'} for _ in range(3)]
    async with memory_upstream(h,entries) as (events,_):
        response=await submit(h)
        row=await activate(h,response.json()['local_run_id'])
        snapshot=row.context_json['api_flow']['memory_prefetch']
        assert snapshot['confirmed_empty'] is False
        assert snapshot['result']['coverage']['state']=='partial'
        assert snapshot['result']['next_offset']==2
        count=len(events)
        recalled=await callback(h,row,'recall_business_memory',{'query':'望远镜'},'supplemental-read')
        assert recalled.json()['result']['data']['items']
        assert len(events)>count


@pytest.mark.parametrize('failure',['timeout','unavailable','disabled'])
async def test_prefetch_failure_or_disabled_never_becomes_empty_memory(harness,failure):
    """Unavailable/disabled memory remains explicit while the existing Agent can handle current input."""
    h=harness
    h.app.state.settings.memory_prefetch_timeout_seconds=.05
    async with memory_upstream(h,failure=failure) as (events,_):
        response=await submit(h)
        assert response.status_code==200,response.text
        row=await stored_run(h,response.json()['local_run_id'])
        snapshot=row.context_json['api_flow']['memory_prefetch']
        assert snapshot['status']==('disabled' if failure=='disabled' else 'unavailable')
        assert snapshot['confirmed_empty'] is False
        assert row.context_json['api_flow']['memory_sources']==[]
        assert h.fake.run_seq==1


async def test_replay_polling_and_concurrent_submission_share_one_prefetch(harness):
    """The durable task key is claimed before remote reading, including overlapping HTTP requests."""
    h=harness
    entered,release=asyncio.Event(),asyncio.Event()
    async with memory_upstream(h,entered=entered,release=release) as (events,_):
        first=asyncio.create_task(submit(h))
        await asyncio.wait_for(entered.wait(),2)
        try:
            duplicate=await submit(h)
            assert duplicate.status_code==202,duplicate.text
            assert len([event for event in events if event['path'].endswith('/memory-settings')])==1
        finally:
            release.set()
        finished=await first
        assert finished.json()['local_run_id']==duplicate.json()['local_run_id']
        count=len([event for event in events if '/me/' in event['path']])
        assert (await submit(h)).json()['local_run_id']==finished.json()['local_run_id']
        await h.client.get('/api/v1/matters/intake/'+finished.json()['local_run_id'],headers=auth(h.token_a))
        assert len([event for event in events if '/me/' in event['path']])==count
        assert h.fake.run_seq==1


async def test_saving_a_fact_invalidates_the_current_task_empty_snapshot(harness):
    """A successful current-task save makes later memory queries read newly saved content."""
    h=harness
    async with memory_upstream(h) as (events,_):
        response=await submit(h,text='请记住：我的望远镜使用前需要校准。')
        row=await activate(h,response.json()['local_run_id'])
        saved=await callback(h,row,'save_business_memory_fact',{'source_quote':'我的望远镜使用前需要校准'},'save-current-fact')
        assert saved.json()['execution_status']=='succeeded',saved.text
        fresh=await callback(h,row,'recall_business_memory',{'query':'望远镜'},'read-saved-fact')
        assert fresh.json()['result']['data']['items'][0]['text']=='我的望远镜使用前需要校准'


async def test_background_task_does_not_automatically_load_personal_memory(harness):
    """Only a new chat input triggers prefetch; persisted scheduled work keeps its original scope."""
    from assistant_api.services import operation_intake
    h=harness
    async with memory_upstream(h) as (events,_):
        async with h.app.state.session_factory() as db:
            user=await db.get(User,h.user_a)
            result=await operation_intake.intake(ExecutionContext(h.app,h.user_a,source='follow_up'),db,user,
                text='检查已有事项进度',idempotency_key='background-memory-scope')
        assert result['status']=='succeeded'
        assert not any('/me/' in event['path'] for event in events)


async def test_historical_memory_tool_bodies_are_not_reinjected_as_current_facts(harness):
    """A disabled/unavailable new lookup cannot be bypassed through stale historical tool receipts."""
    from assistant_api.services.operation_intake import message_history
    h=harness
    async with h.app.state.session_factory() as db:
        db.add(NexusRun(user_id=h.user_a,purpose='matter_plan',idempotency_key='old-memory-history',status='pending',
            request_text='母亲出行',
            context_json={'caller_processed':True,'text':'母亲出行','api_flow':{'history':[],
                'receipts':[{'operation_id':'recall_business_memory','data':{'text':'旧的私人偏好原话'}}]}}))
        await db.commit()
        user=await db.get(User,h.user_a)
        result=await message_history(db,user,'母亲出行',None)
        assert result and result[0]['references']==[]


async def test_full_valid_user_input_can_be_used_for_prefetch_without_truncating_the_task(harness):
    """The 4000-character task interface cannot fail because its memory query previously allowed only 2000."""
    h=harness
    text='资料'*1200+'母亲出行需要少走路'
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'母亲出行时不喜欢坐地铁'}
    async with memory_upstream(h,[fact]) as (events,_):
        response=await submit(h,text=text)
        assert response.status_code==200,response.text
        creation=next(event for event in events if event['path']=='/api/v1/app/runs')
        payload=json.loads(creation['body']['user_request'])
        assert payload['text']==text
        assert payload['memory_context']['result']['items'][0]['entry_id']==fact['entry_id']


async def test_cancelled_prefetch_is_terminal_and_replay_does_not_read_again(harness):
    """A disconnected preparatory request leaves a known failed task without launching a model."""
    h=harness
    entered,release=asyncio.Event(),asyncio.Event()
    async with memory_upstream(h,entered=entered,release=release) as (events,_):
        first=asyncio.create_task(submit(h))
        await asyncio.wait_for(entered.wait(),2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        async with h.app.state.session_factory() as db:
            row=await db.scalar(select(NexusRun).where(NexusRun.idempotency_key=='matter-intake:task-memory-start'))
            assert row.status=='failed' and row.error_code=='memory_prefetch_interrupted'
        reads=len(events)
        repeated=await submit(h)
        assert repeated.json()['status']=='failed'
        assert len(events)==reads and h.fake.run_seq==0


async def test_direct_business_button_does_not_start_a_memory_lookup(harness):
    """Completing an existing todo is a direct business action, not a new natural-language task."""
    from assistant_api.models import Matter, MatterTodo
    h=harness
    async with h.app.state.session_factory() as db:
        matter=Matter(user_id=h.user_a,title='按钮测试事项')
        db.add(matter)
        await db.flush()
        todo=MatterTodo(user_id=h.user_a,matter_id=matter.matter_id,title='直接完成')
        db.add(todo)
        await db.commit()
    async with memory_upstream(h) as (events,_):
        response=await h.client.post(f'/api/v1/matters/{matter.matter_id}/todos/{todo.todo_id}/complete',headers=auth(h.token_a))
        assert response.status_code==200,response.text
        assert response.json()['status']=='done'
        assert events==[]


async def test_foreign_matter_is_rejected_before_prefetch_or_agent_creation(harness):
    """Automatic reads cannot bypass the authenticated ownership of the selected matter."""
    from assistant_api.models import Matter
    h=harness
    async with h.app.state.session_factory() as db:
        matter=Matter(user_id=h.user_b,title='另一个用户的事项')
        db.add(matter)
        await db.commit()
    async with memory_upstream(h) as (events,_):
        response=await h.client.post(f'/api/v1/matters/{matter.matter_id}/intake',headers=auth(h.token_a),
            json={'text':'给我出行建议','idempotency_key':'foreign-matter-prefetch'})
        assert response.status_code==404,response.text
        assert events==[] and h.fake.run_seq==0


async def test_a_new_user_message_refreshes_instead_of_reusing_an_old_empty_task(harness):
    """Empty snapshots belong to one task, so a later user message observes newly stored facts."""
    h=harness
    async with memory_upstream(h) as (events,values):
        first=await submit(h)
        values.append({'entry_id':str(uuid4()),'version':1,'status':'active','text':'母亲出行不喜欢坐地铁'})
        second=await submit(h,key='task-memory-next-message')
        assert first.json()['local_run_id']!=second.json()['local_run_id']
        latest=await stored_run(h,second.json()['local_run_id'])
        assert latest.context_json['memory_context']['result']['items'][0]['text']==values[0]['text']
        assert h.fake.run_seq==2


async def test_changed_prefetched_version_prevents_a_dependent_business_write(harness):
    """Initial candidates enter the same version check as memories read later by Agent tools."""
    from sqlalchemy import func
    from assistant_api.models import MatterTodo
    h=harness
    fact={'entry_id':str(uuid4()),'version':1,'status':'active','text':'母亲出行需要携带材料'}
    async with memory_upstream(h,[fact]) as (_,values):
        response=await submit(h,text='读取母亲出行记忆并创建携带就医材料的待办。')
        row=await activate(h,response.json()['local_run_id'])
        values[0]['version']=2
        write=await callback(h,row,'create_todo',{'title':'携带就医材料','idempotency_key':'prefetch-version-write'},'write-after-change')
        assert write.json()['execution_status']=='rejected',write.text
        assert write.json()['error']['code']=='business_memory_changed'
        async with h.app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(MatterTodo))==0
