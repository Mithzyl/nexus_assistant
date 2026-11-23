"""Exercise capture worker, callback validation and refresh progress without model network calls."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from assistant_api import background
from assistant_api.models import ScheduleEvent
from assistant_api.services import agent_responses
from conftest import auth, tool_auth


def hold(h):
    """Keep the Nexus test workflow in flight until the test delivers a terminal body."""
    h.fake.auto_complete_workflow = False
    h.fake.workflow_release.clear()


async def finish(h, final):
    """Supply the terminal text and let the existing Assistant observer persist it."""
    h.fake.final_text = json.dumps(final, ensure_ascii=False)
    h.fake.release_workflow()


async def status(h, key):
    """Read durable state via the browser's submission-key endpoint."""
    response = await h.client.post('/api/v1/matters/intake/status', headers=auth(h.token_a),
                                   json={'idempotency_key': key})
    return response.json() if response.status_code == 200 else None


async def await_stage(h, key, stage):
    """Wait only for the requested persisted stage in the isolated test."""
    for _ in range(160):
        result = await status(h, key)
        if result and result['stage'] == stage:
            return result
        await asyncio.sleep(0.02)
    raise AssertionError((key, stage, await status(h, key)))


async def callback(h, sequence, call, suffix):
    """Send one fixed model decision through the authenticated Nexus callback."""
    execution_id = f'capture-fixture-{sequence}-{suffix}'
    response = await h.client.post('/internal/nexus/agent-responses', headers=tool_auth(h.settings), json={
        'protocol_version': '1', 'application_id': 'personal_assistant', 'external_user_id': h.user_a,
        'run_id': f'run_remote_{sequence}', 'workflow_id': f'wf_remote_{sequence}',
        'response_id': execution_id, 'execution_id': execution_id,
        'content': json.dumps(call, ensure_ascii=False),
    })
    assert response.status_code == 200
    return response.json()


def create_proposal(title, start):
    """Provide a fixed half-hour schedule using the published operation body."""
    return {'decision': 'call', 'call': {'operation_id': 'create_schedule', 'body': {
        'title': title, 'starts_at': start.isoformat(),
        'ends_at': (start + timedelta(minutes=30)).isoformat(), 'idempotency_key': 'model-key',
    }}}


async def count_schedules(h):
    """Count the actual isolated account's schedule rows after commit or rollback."""
    async with h.app.state.session_factory() as db:
        return await db.scalar(select(func.count()).select_from(ScheduleEvent).where(ScheduleEvent.user_id == h.user_a))


async def test_worker_saves_one_schedule_and_checks_again_after_conflict_choice(harness):
    """Run the actual shared worker, preserving zero writes when both original and new slots conflict."""
    h = harness
    start = datetime(2041, 4, 9, 2, tzinfo=UTC)
    headers = auth(h.token_a)
    queue = '/api/v1/matters/intake'

    async def accept(key, text):
        """Enqueue via the public API and then let the real shared worker consume it."""
        hold(h)
        response = await h.client.post(queue, headers=headers,
            json={'text': text, 'idempotency_key': key, 'entry_mode': 'async_capture'})
        assert response.status_code == 202
        assert await background.tick(h.app, 'worker', 'capture-acceptance')
        return response.json()['record_id']

    original_count = await count_schedules(h)
    await accept('cap-success', '上午十点开评审')
    saved = await callback(h, 1, create_proposal('项目评审', start), 'create')
    assert saved['execution_status'] == 'succeeded'
    assert saved['result']['data']['schedule']['title'] == '项目评审'
    await finish(h, {'decision':'answer','answer':'已保存项目评审'})
    assert (await await_stage(h, 'cap-success', 'completed'))['status'] == 'completed'
    assert await count_schedules(h) == original_count + 1

    today = '/api/v1/today?date=2041-04-09'
    old = await accept('cap-conflict', '上午十点去银行')
    failed = await callback(h, 2, create_proposal('去银行', start), 'conflict')
    assert failed['error']['code'] == 'conflict_detected'
    details = failed['result']['data']['error']['details']
    assert details['formal_write_status'] == 'not_applied'
    blocked = await callback(h, 2, create_proposal('去银行', start + timedelta(hours=3)), 'auto-replan')
    assert blocked['error']['code'] == 'capture_reply_required'
    await finish(h, {'decision':'ask_user','question':'改到下午两点？','choices':['改到下午两点']})
    await await_stage(h, 'cap-conflict', 'awaiting_reply')
    assert await count_schedules(h) == original_count + 1
    view = (await h.client.get(today, headers=headers)).json()
    assert any(item['title'] == '项目评审' for item in view['schedules'])
    draft = next(item for item in view['capture_replies'] if item['local_run_id'] == old)
    assert draft['request_text'] == '上午十点去银行' and '项目评审' in draft['question']

    # The proposed alternative is now occupied. Even the old draft interface,
    # whose request permits an acknowledgement, cannot bypass a fresh check.
    old_preview = await h.client.post(f"/api/v1/matters/drafts/{details['draft']['draft_id']}/confirm",
        headers={**headers, 'Idempotency-Key':'legacy-capture-confirm'},
        json={'expected_version':details['draft']['version'], 'acknowledge_conflict':True})
    assert old_preview.status_code == 409
    alternative = start + timedelta(hours=4)
    inserted = await h.client.post('/api/v1/schedules', headers=headers,
        json=create_proposal('临时会议', alternative)['call']['body'])
    assert inserted.status_code == 201
    hold(h)
    reply = asyncio.create_task(h.client.post(queue, headers=headers, json={
        'text':'改到下午两点','idempotency_key':'cap-reply-new-slot','continuation_run_id':old,
    }))
    try:
        for _ in range(100):
            if h.fake.workflow_seq == 3:
                break
            await asyncio.sleep(0.02)
        again = await callback(h, 3, create_proposal('去银行', alternative), 'new-conflict')
        assert again['error']['code'] == 'conflict_detected'
        assert again['result']['data']['error']['details']['formal_write_status'] == 'not_applied'
    finally:
        await finish(h, {'decision':'ask_user','question':'下午两点也有冲突，改到三点？','choices':['改到下午三点']})
        response = await reply
    assert await count_schedules(h) == original_count + 2
    new = response.json()
    assert new['outcome'] == 'needs_clarification' and new['parent_run_id'] == old
    view = (await h.client.get(today, headers=headers)).json()
    assert {item['local_run_id'] for item in view['capture_replies']} == {new['local_run_id']}


async def test_status_recovers_real_tool_progress_by_original_key(harness, monkeypatch):
    """Observe a gated real tool dispatch and the final saved result with the same request ID."""
    h = harness
    hold(h)
    started, release = asyncio.Event(), asyncio.Event()
    real_execute = agent_responses.execute_call

    async def delayed_dispatch(*args, **kwargs):
        """Pause between committed progress and the real business handler."""
        started.set()
        await release.wait()
        return await real_execute(*args, **kwargs)

    monkeypatch.setattr(agent_responses, 'execute_call', delayed_dispatch)
    key = 'observed-chat-id'
    request = asyncio.create_task(h.client.post('/api/v1/matters/intake', headers=auth(h.token_a),
        json={'text':'四月十日开会','idempotency_key':key}))
    tool = None
    try:
        first = await await_stage(h, key, 'understanding')
        for _ in range(100):
            if h.fake.workflow_seq == 1:
                break
            await asyncio.sleep(0.02)
        tool = asyncio.create_task(callback(h, 1, create_proposal(
            '阶段测试会议', datetime(2041, 4, 10, 2, tzinfo=UTC)), 'gated'))
        await asyncio.wait_for(started.wait(), 3)
        during = await await_stage(h, key, 'executing_tool')
        assert during['local_run_id'] == first['local_run_id']
        denied = await h.client.post('/api/v1/matters/intake/status', headers=auth(h.token_b),
                                    json={'idempotency_key':key})
        assert denied.status_code == 404
    finally:
        release.set()
        if tool:
            assert (await tool)['execution_status'] == 'succeeded'
            final = await callback(h, 1, {'decision':'answer','answer':'已安排阶段测试会议'}, 'final')
            assert final['disposition'] == 'complete'
            assert (await await_stage(h, key, 'generating_reply'))['local_run_id'] == first['local_run_id']
        await finish(h, {'decision':'answer','answer':'已安排阶段测试会议'})
        await request
    complete = await await_stage(h, key, 'completed')
    assert complete['local_run_id'] == first['local_run_id']
    assert h.fake.run_seq == 1


async def test_common_schedule_tools_save_one_model_round_with_fixed_decisions(harness):
    """Compare the actual callback sequence for the former and current first-message definitions."""
    from assistant_api.models import NexusRun
    from assistant_api.services import operation_intake
    from assistant_api.services.api_operations import OperationCatalog

    h = harness
    catalog = OperationCatalog(h.app)
    for case, (operation_id, legacy) in enumerate((
        ('list_schedules', True), ('list_schedules', False),
        ('create_schedule', True), ('create_schedule', False),
    ), start=1):
        flow = {'text':'日程接口定向检查','now':'2041-04-10T00:00:00+00:00',
                'timezone':'Asia/Shanghai','history':[],'step':0,'receipts':[],'trigger_source':'chat'}
        context = operation_intake.model_context(flow, catalog)
        if legacy:
            context['api_operations'] = context['api_operations'][:1]
            resolver_schemas = catalog.operations['resolve_operation_definitions'].components
            context['components'] = {'schemas':resolver_schemas}
        context.update(caller_processed=True, api_flow=flow)
        async with h.app.state.session_factory() as db:
            db.add(NexusRun(user_id=h.user_a, nexus_run_id=f'run_remote_{case}',
                            workflow_execution_id=f'wf_remote_{case}', idempotency_key=f'rounds-{case}',
                            status='running', request_text=flow['text'], context_json=context))
            await db.commit()
        proposal = ({'decision':'call','call':{'operation_id':operation_id}} if operation_id == 'list_schedules'
                    else create_proposal(f'创建测试-{case}', datetime(2041, 4, case + 12, 8, tzinfo=UTC)))
        rounds = []
        if legacy:
            rounds.append(await callback(h, case, {'decision':'call','call':{
                'operation_id':'resolve_operation_definitions',
                'body':{'uris':[catalog.operations[operation_id].uri]},
            }}, 'resolve'))
        rounds.append(await callback(h, case, proposal, 'business'))
        rounds.append(await callback(h, case, {'decision':'answer','answer':'已核对处理结果。'}, 'final'))
        assert all(result['execution_status'] == 'succeeded' for result in rounds[:-1])
        assert rounds[-1]['disposition'] == 'complete'
        assert len(rounds) == (3 if legacy else 2)


async def test_capture_migration_preserves_reply_schema_and_old_rows(tmp_path):
    """Apply 0032 to a file whose 0031 reply state and existing records are already in place."""
    import sqlite3

    from scripts.migrate_sqlite import init, upgrade

    path = tmp_path / 'old-intake.sqlite3'
    url = f'sqlite+aiosqlite:///{path}'
    await init(url)
    with sqlite3.connect(path) as db:
        db.execute('DROP INDEX ix_nexus_runs_intake_queue')
        for column in ('intake_stage', 'capture_lease_expires_at', 'capture_dispatch_started_at'):
            db.execute(f'ALTER TABLE nexus_runs DROP COLUMN {column}')
        db.execute("DELETE FROM schema_migrations WHERE version='0032_intake_capture'")
        db.execute("""INSERT INTO nexus_runs (local_run_id,user_id,idempotency_key,status,
            last_event_sequence,request_text,created_at,updated_at,reply_state,reply_expires_at)
            VALUES ('reply-old','old-user','old-reply','succeeded',0,'原追问',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'resolved','2041-04-09 03:00:00')""")
    await upgrade(url)
    with sqlite3.connect(path) as db:
        versions = {row[0] for row in db.execute('SELECT version FROM schema_migrations')}
        assert {'0031_intake_replies', '0032_intake_capture'} <= versions
        assert db.execute("""SELECT request_text,reply_state,reply_expires_at,intake_stage
            FROM nexus_runs WHERE local_run_id='reply-old'""").fetchone() == (
                '原追问', 'resolved', '2041-04-09 03:00:00', None)


async def test_worker_does_not_redispatch_an_interrupted_capture(harness):
    """An expired capture with dispatched intent but no Nexus ID is classified as unknown."""
    from assistant_api.models import NexusRun
    from assistant_api.services import capture_intake

    h = harness
    headers = auth(h.token_a)
    payload = {'text':'保存中途断线','entry_mode':'async_capture','idempotency_key':'interrupted-capture'}
    accepted = await h.client.post('/api/v1/matters/intake', headers=headers, json=payload)
    run_id = accepted.json()['record_id']
    async with h.app.state.session_factory() as db:
        row = await db.get(NexusRun, run_id)
        row.intake_stage = 'understanding'
        row.capture_dispatch_started_at = datetime.now(UTC) - timedelta(days=1)
        row.capture_lease_expires_at = row.capture_dispatch_started_at + timedelta(minutes=2)
        await db.commit()
    assert not await capture_intake.worker_tick(h.app)
    assert (await status(h, payload['idempotency_key']))['stage'] == 'unknown'
    assert not await capture_intake.worker_tick(h.app)
    assert h.fake.run_seq == 0
