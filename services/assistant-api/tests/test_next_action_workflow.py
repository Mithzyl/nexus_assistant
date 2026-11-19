"""One isolated recommendation workflow using published business definitions and fixed Nexus decisions."""
import base64
import json
import os
import sqlite3
from datetime import UTC, datetime

from sqlalchemy import select

import pytest

from conftest import auth, tool_auth
from assistant_api.models import BusinessMemoryApplication, NexusRun
from assistant_api.services.memory_repository import MemoryStore
from assistant_api.services.operation_intake import model_context
from assistant_api.services.api_operations import OperationCatalog


@pytest.mark.asyncio
async def test_next_action_workflow(harness, monkeypatch, tmp_path):
    """Create a relative deadline by tool call, move its meeting, and use recommendation actions."""
    h = harness
    from scripts.migrate_sqlite import upgrade
    legacy = tmp_path / 'legacy.db'
    with sqlite3.connect(legacy) as db:
        db.execute("CREATE TABLE matter_todos (todo_id TEXT PRIMARY KEY, title TEXT, due_at DATETIME, deleted_at DATETIME)")
        db.execute("INSERT INTO matter_todos (todo_id,title,due_at) VALUES ('legacy','保留旧待办','2030-01-02 01:05:00')")
    await upgrade(f'sqlite+aiosqlite:///{legacy}')
    with sqlite3.connect(legacy) as db:
        assert db.execute("SELECT title,due_at,priority,due_schedule_id FROM matter_todos").fetchone()==(
            '保留旧待办','2030-01-02 01:05:00',None,None)
        assert db.execute("SELECT version FROM schema_migrations WHERE version='0030_next_actions'").fetchone()
    from assistant_api.services import next_actions
    monkeypatch.setattr(next_actions, 'server_now', lambda: datetime(2030, 1, 2, 1, tzinfo=UTC))
    headers = auth(h.token_a)

    async def send(method, path, body=None, *, token_headers=headers, expect=200):
        """Call the actual HTTP route and check its response before reading business fields."""
        response = await h.client.request(method, '/api/v1' + path, headers=token_headers, json=body)
        assert response.status_code == expect, response.text
        return response.json()

    windows = {'timezone':'Asia/Shanghai','windows':[
        {'weekday':3,'starts_local':'09:00','ends_local':'18:00','kind':'available'},
        {'weekday':3,'starts_local':'09:30','ends_local':'10:00','kind':'unavailable'}]}
    await send('PUT', '/availability', {**windows, 'expected_version':0})
    meeting = await send('POST', '/schedules', {'title':'采购会议','idempotency_key':'recommendation-meeting',
        'starts_at':'2030-01-02T09:50:00+08:00','ends_at':'2030-01-02T10:05:00+08:00'}, expect=201)
    sid, mid = meeting['schedule']['schedule_id'], meeting['matter']['matter_id']
    key = tmp_path / 'memory.key'
    key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
    factory = h.app.state.session_factory
    store = MemoryStore(factory,key)
    h.app.state.memory_store = store
    factory.configure(info={'memory_store':store})
    quote = '准备采购材料通常需要25分钟。'
    memory = await send('POST', '/memory/commands', {'action':'save','key':'recommendation-memory',
        'text':quote})
    create = {'title':'准备采购材料','matter_id':mid,'expected_matter_version':meeting['matter']['version'],
        'idempotency_key':'recommendation-create', 'priority':'high',
        'available_after':'2030-01-02T09:00:00+08:00','place':{'label':'资料室'},
        'schedule_due':{'schedule_id':sid,'offset_seconds':-1800},
        'reminder_at':'2030-01-02T09:05:00+08:00',
        'memory_defaults':{'estimated_duration_minutes':{'entry_id':memory['entry_id'],
            'version':memory['applied_version'],'value':25,'source_quote':quote,'reason':'用户只要求沿用资料准备耗时'}}}
    catalog = OperationCatalog(h.app)
    flow = {'text':'在资料室优先准备采购材料，耗时按以往习惯，采购会议前半小时完成。',
        'now':'2030-01-02T01:00:00Z','timezone':'Asia/Shanghai','history':[],
        'receipts':[],'results':{},'visible_result':None,'step':0,
        'memory_sources':[{'entry_id':memory['entry_id'],'version':memory['applied_version']}]}
    ctx = model_context(flow,catalog,catalog_query='新增待办 下一步 设置作息')
    assert catalog.operations['create_todo'].uri in {item['uri'] for item in ctx['operation_index']['items']}
    async with factory() as db:
        db.add(NexusRun(user_id=h.user_a,nexus_run_id='next-actions-run',workflow_execution_id='next-actions-workflow',
            idempotency_key='next-actions-agent',request_text=flow['text'],status='running',
            context_json={**ctx,'caller_processed':True,'api_flow':flow}))
        await db.commit()

    async def deliver(execution, decision):
        """Submit fixed model output through the published, application-authenticated callback."""
        r = await h.client.post('/internal/nexus/agent-responses',headers=tool_auth(h.settings),json={
            'protocol_version':'1','application_id':'personal_assistant','external_user_id':h.user_a,
            'run_id':'next-actions-run','workflow_id':'next-actions-workflow','response_id':execution,
            'execution_id':execution,'content':json.dumps(decision,ensure_ascii=False)})
        assert r.status_code==200, r.text
        return r.json()

    resolved = await deliver('next-actions-resolve',{'decision':'call','call':{
        'operation_id':'resolve_operation_definitions','body':{'uris':[catalog.operations[name].uri for name in
            ('create_todo','query_next_actions','command_next_action','save_availability')]}}})
    assert resolved['execution_status']=='succeeded', resolved
    created = await deliver('next-actions-create',{'decision':'call','call':{'operation_id':'create_todo',
        'body':create,'summary':'将截止绑定采购会议，并采用已检索的准备耗时'}})
    assert created['execution_status']=='succeeded', created
    assert created['result']['data']['todo']['estimated_duration_minutes']==25
    async with factory() as db:
        provenance = await db.scalar(select(BusinessMemoryApplication).where(BusinessMemoryApplication.user_id==h.user_a))
        assert store.decrypt(h.user_a,provenance.body)['actual_values']=={'estimated_duration_minutes':25}
    queried = await deliver('next-actions-query',{'decision':'call','call':{'operation_id':'query_next_actions'}})
    assert queried['execution_status']=='succeeded', queried
    assert queried['result']['data']['primary']['title']=='准备采购材料'
    final = await deliver('next-actions-answer',{'decision':'answer','answer':'准备材料的待办已保存。','cards':[]})
    assert final['disposition']=='complete', final
    records = (await send('GET', f'/todos?matter_id={mid}'))['items']
    assert len(records)==1 and records[0]['due_at'].startswith('2030-01-02T01:20')
    assert records[0]['priority']=='high' and records[0]['place']['label']=='资料室'
    tid = records[0]['todo_id']
    calls = len(h.fake.calls)
    first = await send('POST', '/next-actions/query')
    assert len(h.fake.calls)==calls
    assert first['primary']['record_id']==tid and first['primary']['state']=='deadline_risk'
    assert first['primary']['estimated_duration_minutes']==25
    command = {'recommendation_id':first['recommendation_id'],'expected_recommendation_version':first['version'],
               'kind':'todo','record_id':tid,'action':'snooze','until':'2030-01-02T09:15:00+08:00',
               'idempotency_key':'recommendation-snooze'}
    await send('POST', '/next-actions/commands',command,token_headers=auth(h.token_b),expect=404)
    current = await send('GET', f'/schedules/{sid}')
    await send('PATCH', f'/schedules/{sid}',{'expected_matter_version':current['matter']['version'],
        'idempotency_key':'recommendation-reschedule','starts_at':'2030-01-02T12:00:00+08:00','ends_at':'2030-01-02T12:20:00+08:00'})
    await send('POST','/next-actions/commands', command,expect=409)
    saved = await send('GET',f'/todos/{tid}')
    assert saved['todo']['due_at'].startswith('2030-01-02T03:30')
    assert saved['todo']['reminder_at'].startswith('2030-01-02T01:05')
    fresh = await send('POST','/next-actions/query')
    assert fresh['primary']['record_id']==tid and fresh['primary']['state']=='fits_window'
    command.update(recommendation_id=fresh['recommendation_id'],expected_recommendation_version=fresh['version'])
    receipt = await send('POST','/next-actions/commands',command)
    assert await send('POST','/next-actions/commands',command)==receipt
    hidden = await send('POST','/next-actions/query')
    assert hidden['suppressed'][0]['record_id']==tid and hidden['primary']['record_id']!=tid
    await send('POST','/next-actions/commands',{**command,'recommendation_id':hidden['recommendation_id'],
        'expected_recommendation_version':hidden['version'],'action':'resume','until':None,'idempotency_key':'recommendation-resume'})
    await send('PATCH',f'/todos/{tid}',{'expected_matter_version':saved['matter']['version'],
        'idempotency_key':'recommendation-complete','status':'done'})
    done = await send('POST','/next-actions/query')
    assert all(item is None or item['record_id']!=tid for item in done['alternatives'] + [done['primary']])
    assert len(h.fake.calls)==calls
