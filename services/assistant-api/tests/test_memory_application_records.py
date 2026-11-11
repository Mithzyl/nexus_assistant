"""Targeted memory-attribution checks with isolated SQLite and fixed Agent intentions.

Run only this file. No real model, map, business database, regression or hash check.
"""
import json
from uuid import uuid4

from sqlalchemy import select

from conftest import auth
from test_owned_memory import owned, command
from assistant_api.models import NexusRun


def selection(entry, value, **extra):
    """Describe a model-chosen value with an exact owned quote and optional business inputs."""
    return {'entry_id': entry['entry_id'], 'version': entry['version'],
            'source_quote': entry['text'], 'value': value, 'reason': '按本次任务选择此记忆。', **extra}


async def fact(h, text):
    """Save a real isolated owned memory and return the normal public record."""
    result = await command(h, action='save', text=text)
    assert result.status_code == 200, result.text
    return (await h.client.get('/api/v1/memory', headers=auth(h.token))).json()['items'][-1]


async def applications(h):
    """Read encrypted attribution records directly, never add a frontend source endpoint."""
    from assistant_api.models import BusinessMemoryApplication
    async with h.app.state.session_factory() as db:
        rows = list(await db.scalars(select(BusinessMemoryApplication).order_by(BusinessMemoryApplication.created_at)))
        store = h.app.state.memory_store
        return [store.decrypt(row.user_id, row.body) for row in rows]


async def test_todo_sources_replay_reapply_and_private_response(owned):
    """Create and explicitly reapply a memory to the same todo, retaining both actual applications."""
    h = owned
    entry = await fact(h, '报销资料按本次会议前半小时完成。')
    schedule = (await h.client.post('/api/v1/schedules', headers=auth(h.token), json={
        'title': '讨论会', 'starts_at': '2030-11-11T10:00:00+08:00', 'idempotency_key': 'source-meeting'})).json()
    body = {'title': '整理资料', 'idempotency_key': 'source-todo', 'memory_defaults': {
        'due_at': selection(entry, '2030-11-11T09:30:00+08:00', references=[{
            'kind': 'schedule', 'record_id': schedule['schedule']['schedule_id'],
            'version': schedule['matter']['version']}])}}
    one = await h.client.post('/api/v1/todos', headers=auth(h.token), json=body)
    assert one.status_code == 201, one.text
    replay = await h.client.post('/api/v1/todos', headers=auth(h.token), json=body)
    assert replay.status_code == 201 and replay.json()['todo']['todo_id'] == one.json()['todo']['todo_id']
    assert entry['text'] not in one.text
    records = await applications(h)
    assert len(records) == 1 and records[0]['scope'] == 'creation'
    detail = records[0]['receipt']['fields']['due_at']
    assert detail['entry_id'] == entry['entry_id'] and detail['version'] == 1
    assert detail['references'][0]['snapshot']['starts_at'].startswith('2030-11-11T02:00:00')
    changed = await command(h, action='correct', entry_id=entry['entry_id'], expected_version=1,
                            text='本次改为会议前一小时完成资料。')
    assert changed.status_code == 200
    entry = (await h.client.get('/api/v1/memory', headers=auth(h.token))).json()['items'][0]
    updated = await h.client.patch('/api/v1/todos/' + one.json()['todo']['todo_id'], headers=auth(h.token), json={
        'idempotency_key': 'source-reapply', 'expected_matter_version': one.json()['matter']['version'],
        'memory_defaults': {'due_at': selection(entry, '2030-11-11T09:00:00+08:00')}})
    assert updated.status_code == 200, updated.text
    assert updated.json()['todo']['todo_id'] == one.json()['todo']['todo_id']
    records = await applications(h)
    assert len(records) == 2 and records[-1]['scope'] == 'reapplication'
    assert records[0]['receipt']['fields']['due_at']['version'] == 1
    assert records[-1]['receipt']['fields']['due_at']['version'] == 2
    assert records[-1]['receipt']['application_status'] == 'applied'
    assert records[-1]['previous_values']['due_at'].startswith('2030-11-11T01:30:00')
    assert entry['text'] not in updated.text and not h.calls


async def test_combination_checks_sources_again_at_save(owned):
    """One combined draft binds each selected field; changed memory prevents all formal writes."""
    h = owned
    entry = await fact(h, '讨论前提前十分钟提醒，整理材料九点截止。')
    body = {'idempotency_key': 'combined-sources', 'title': '会前安排',
        'schedule': {'title': '讨论', 'starts_at': '2030-11-11T10:00:00+08:00',
                     'memory_defaults': {'reminder_offset_seconds': selection(entry, -600)}},
        'todos': [{'title': '材料', 'memory_defaults': {'due_at': selection(entry, '2030-11-11T09:00:00+08:00')}}]}
    result = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token), json=body)
    assert result.status_code == 201, result.text
    draft = result.json()
    assert entry['text'] not in result.text
    await command(h, action='correct', entry_id=entry['entry_id'], expected_version=1, text='本次习惯已经改变。')
    rejected = await h.client.post('/api/v1/matters/drafts/' + draft['draft_id'] + '/confirm',
        headers={**auth(h.token), 'Idempotency-Key': 'combined-confirm'}, json={'expected_version': draft['version']})
    assert rejected.status_code == 409, rejected.text
    assert (await h.client.get('/api/v1/todos', headers=auth(h.token))).json()['items'] == []
    assert await applications(h) == []


async def test_combination_and_schedule_reapplication_keep_sources(owned):
    """Save schedule and todo sources atomically, then reapply explicitly without changing IDs or other fields."""
    h = owned
    entry = await fact(h, '日程提前十二分钟提醒，材料九点截止。')
    result = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token), json={
        'idempotency_key': 'combination-ok', 'title': '组合事项',
        'schedule': {'title': '讨论', 'starts_at': '2030-11-11T10:00:00+08:00',
                     'memory_defaults': {'reminder_offset_seconds': selection(entry, -720)}},
        'todos': [{'title': '材料', 'memory_defaults': {'due_at': selection(entry, '2030-11-11T09:00:00+08:00')}}]})
    assert result.status_code == 201, result.text
    draft = result.json()
    schedule_fields = dict(draft['payload']['schedule'])
    schedule_fields.pop('reminder_at', None)
    schedule_fields.pop('reminder_offset_seconds', None)
    schedule_fields['memory_defaults'] = {'reminder_offset_seconds': selection(entry, -720)}
    patched = await h.client.patch('/api/v1/matters/drafts/' + draft['draft_id'], headers=auth(h.token), json={
        'expected_version': draft['version'], 'schedule': schedule_fields})
    assert patched.status_code == 200, patched.text
    draft = patched.json()
    result = await h.client.post('/api/v1/matters/drafts/' + draft['draft_id'] + '/confirm',
        headers={**auth(h.token), 'Idempotency-Key': 'combined-ok-save'}, json={'expected_version': draft['version']})
    assert result.status_code == 200, result.text
    assert len(await applications(h)) == 2
    saved = result.json()
    schedule_id = saved['schedule']['schedule_id']
    updated = await h.client.patch('/api/v1/schedules/' + schedule_id, headers=auth(h.token), json={
        'idempotency_key': 'schedule-reapply', 'expected_matter_version': saved['matter']['version'],
        'memory_defaults': {'reminder_offset_seconds': selection(entry, -720)}})
    assert updated.status_code == 200, updated.text
    assert updated.json()['schedule']['starts_at'] == saved['schedule']['starts_at']
    assert (await applications(h))[-1]['scope'] == 'reapplication'
    assert entry['text'] not in updated.text and not h.calls


async def test_recurring_plan_sources_and_explicit_reapplication(owned):
    """Persist creation and reapplication sources with the same plan transaction and no public quote fields."""
    h = owned
    entry = await fact(h, '通勤默认步行，改动本次计划前明确选择。')
    config = {'name': '工作日通勤', 'timezone': 'Asia/Shanghai', 'weekdays': [1,2,3,4,5],
        'arrival_local_time': '09:00', 'check_local_time': '08:00',
        'origin': {'label': '起点', 'latitude': 31.2, 'longitude': 121.4},
        'destination': {'label': '终点', 'latitude': 31.21, 'longitude': 121.41},
        'memory_defaults': {'transport_mode': selection(entry, 'walking')}}
    one = await h.client.post('/api/v1/commute-plans', headers=auth(h.token), json=config)
    assert one.status_code == 201, one.text
    assert one.json()['transport_mode'] == 'walking' and entry['text'] not in one.text
    plan_id = one.json()['id']
    config['name'] = '明确重新应用的通勤'
    changed = await h.client.put('/api/v1/commute-plans/' + plan_id,
        headers={**auth(h.token), 'If-Match': '1'}, json=config)
    assert changed.status_code == 200, changed.text
    records = await applications(h)
    assert len(records) == 2 and records[-1]['scope'] == 'reapplication'
    assert records[-1]['target_id'] == plan_id and records[-1]['target_revision'] == 2
    assert not h.calls


async def test_additive_attribution_migration_preserves_owned_data(owned):
    """Upgrade an isolated pre-attribution database while retaining accounts and existing encrypted memories."""
    from scripts.migrate_sqlite import upgrade
    from assistant_api.models import BusinessMemoryApplication, MemoryEntry, User
    h = owned
    entry = await fact(h, '原账号的历史记忆必须保留。')
    async with h.app.state.engine.begin() as connection:
        await connection.run_sync(BusinessMemoryApplication.__table__.drop)
    await upgrade(f'sqlite+aiosqlite:///{h.path}/memory.db')
    async with h.app.state.session_factory() as db:
        assert len(list(await db.scalars(select(User)))) == 2
        assert (await db.get(MemoryEntry, entry['entry_id'])).entry_version == 1
        assert list(await db.scalars(select(BusinessMemoryApplication))) == []
    assert (await h.client.get('/api/v1/memory', headers=auth(h.token))).json()['items'][0]['text'] == entry['text']
    assert not h.calls


async def test_conflicting_sources_and_agent_identity_are_checked(owned):
    """Reject ambiguous, foreign, stale and not-retrieved selections before any attribution or business write."""
    h = owned
    entry = await fact(h, '提醒我九点整理资料。')
    body = {'title': '资料', 'idempotency_key': 'duplicate-sources', 'due_at': None,
            'memory_defaults': {'due_at': selection(entry, '2030-11-11T09:00:00+08:00')}}
    rejected = await h.client.post('/api/v1/todos', headers=auth(h.token), json=body)
    assert rejected.status_code in (409,422), rejected.text
    body.pop('due_at')
    rejected = await h.client.post('/api/v1/todos', headers=auth(h.other), json=body)
    assert rejected.status_code in (404,409), rejected.text
    from assistant_api.services.agent_responses import process_response
    async with h.app.state.session_factory() as db:
        row = NexusRun(local_run_id=str(uuid4()), user_id=h.a, nexus_run_id=str(uuid4()),
            workflow_execution_id=str(uuid4()), idempotency_key='source-agent', request_text='按习惯创建资料待办',
            status='running', context_json={'caller_processed': True, 'api_flow': {'text': '按习惯创建资料待办',
                'trigger_source': 'chat', 'offered_operations': ['create_todo', 'recall_business_memory'], 'memory_sources': [],
                'receipts': [], 'results': {}, 'visible_result': None, 'step': 0}})
        db.add(row)
        await db.commit()
        result = await process_response(db, h.app, {'protocol_version': '1', 'application_id': 'personal_assistant',
            'external_user_id': h.a, 'run_id': row.nexus_run_id, 'workflow_id': row.workflow_execution_id,
            'response_id': 'source-call', 'execution_id': 'source-call', 'content': json.dumps({'decision': 'call',
                'call': {'operation_id': 'create_todo', 'body': body, 'summary': '按习惯创建资料待办'}})})
        assert result['execution_status'] == 'failed', result
    assert (await h.client.get('/api/v1/todos', headers=auth(h.token))).json()['items'] == []
    assert await applications(h) == [] and not h.calls
    envelope = {'protocol_version': '1', 'application_id': 'personal_assistant', 'external_user_id': h.a,
                'run_id': row.nexus_run_id, 'workflow_id': row.workflow_execution_id}
    for execution, operation, arguments in [('source-recall', 'recall_business_memory', {'query': '提醒'}),
                                            ('source-create', 'create_todo', body)]:
        async with h.app.state.session_factory() as db:
            result = await process_response(db, h.app, {**envelope, 'response_id': execution, 'execution_id': execution,
                'content': json.dumps({'decision': 'call', 'call': {'operation_id': operation, 'body': arguments,
                                                                  'summary': '使用真实检索来源创建待办'}})})
        assert result['execution_status'] == 'succeeded', result
    assert (await applications(h))[0]['receipt']['fields']['due_at']['selected_by'] == 'ai'
    assert not h.calls


async def test_commute_bundle_sources_survive_quote_and_confirmation(owned, monkeypatch):
    """Verify memory choices across the real bundle/save flow using fixed offline place/route samples only."""
    from datetime import UTC, datetime, timedelta
    from assistant_api.services import commute_places, commute_estimate, commute_memory
    h = owned
    entry = await fact(h, '通勤九点到达，出行默认步行。')

    async def places(*args, **kwargs):
        """Keep external place lookup outside this provenance check; selection below is server-signed sample data."""
        return {'status': 'unavailable', 'candidates': []}

    async def route(*args, **kwargs):
        """Return one fixed route estimate without contacting any map or model provider."""
        return {'status': 'available', 'duration_seconds': 600, 'mode': kwargs['mode'], 'provider': 'fixture',
                'fetched_at': datetime.now(UTC).isoformat(), 'shape': {'status': 'missing', 'segments': []}}

    async def no_extraction(*args, **kwargs):
        """Exclude the unrelated legacy post-confirm model extractor from this fixed-intention test."""
        return None

    monkeypatch.setattr(commute_places, 'search_places', places)
    monkeypatch.setattr(commute_estimate, 'estimate', route)
    monkeypatch.setattr(commute_memory, 'start', no_extraction)
    created = await h.client.post('/api/v1/matters/commute-drafts', headers=auth(h.token), json={
        'idempotency_key': 'commute-memory-preview', 'origin_query': '起点', 'destination_query': '终点', 'city': '上海',
        'memory_defaults': {'arrival_local_time': selection(entry, '09:00')}})
    assert created.status_code == 201, created.text
    draft = created.json()
    assert 'memory_application_envelope' not in created.text
    assert entry['text'] not in created.text
    point = {'name': '样本地点', 'address': '样本地址', 'city': '上海', 'latitude': 31.2, 'longitude': 121.4,
             'coordinate_system': 'gcj02', 'source': 'fixture'}
    token = commute_places.sign_selection(point, h.app.state.settings.tool_callback_token.get_secret_value(),
                                          user_id=h.a, draft_id=draft['draft_id'], selection_kind='place')
    day = (datetime.now(UTC) + timedelta(days=5)).date()
    quoted = await h.client.post('/api/v1/matters/drafts/' + draft['draft_id'] + '/commute-quote', headers=auth(h.token), json={
        'expected_version': draft['version'], 'origin_token': token, 'destination_token': token,
        'effective_from': str(day), 'weekdays': [day.isoweekday()], 'buffer_minutes': 10,
        'memory_defaults': {'mode': selection(entry, 'walking')}, 'test_only': True})
    assert quoted.status_code == 200, quoted.text
    saved = await h.client.post('/api/v1/matters/drafts/' + draft['draft_id'] + '/confirm',
        headers={**auth(h.token), 'Idempotency-Key': 'commute-memory-confirm'},
        json={'expected_version': quoted.json()['version']})
    assert saved.status_code == 200, saved.text
    records = await applications(h)
    assert len(records) == 1 and records[0]['kind'] == 'commute_plan'
    assert set(records[0]['receipt']['fields']) == {'arrival_local_time', 'mode'}
    assert not h.calls
