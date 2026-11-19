"""Targeted Agent repair checks with temporary SQLite and no live Nexus/model calls.

Run: .venv/bin/python -m pytest tests/test_agent_repair_feedback.py
Only this file's callback, transaction and lost-response scenarios are exercised.
"""
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from assistant_api.main import create_app
from assistant_api.models import Matter, MatterDraft, MatterTodo, NexusRun, ScheduleEvent
from assistant_api.services import agent_responses
from assistant_api.services.api_operations import OperationCatalog
from assistant_api.services.operation_intake import workflow_configuration
from conftest import FakeNexus, auth, build_settings, login, seed_user, tool_auth


@pytest_asyncio.fixture
async def repair_app(tmp_path):
    """Use a separate database and a fake upstream; never touch running services or existing data."""
    fake = FakeNexus()
    settings = build_settings().model_copy(update={
        'database_url': f'sqlite+aiosqlite:///{tmp_path}/repair.db',
    })
    app = create_app(settings, nexus_transport=httpx.MockTransport(fake.handler))
    async with app.router.lifespan_context(app):
        user = await seed_user(app, email='repair@example.test', display_name='定向验证')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://assistant.test') as client:
            token = await login(client, 'repair@example.test')
            async with app.state.session_factory() as db:
                run = NexusRun(user_id=user, nexus_run_id='repair-run', workflow_execution_id='repair-workflow',
                    idempotency_key='repair-task', request_text='安排日程，遇到冲突先询问，不重复创建。',
                    status='running', context_json={'caller_processed': True, 'api_flow': {
                        'text': '安排日程，遇到冲突先询问，不重复创建。', 'trigger_source': 'chat',
                        'offered_operations': ['create_todo', 'create_schedule', 'update_schedule'],
                        'receipts': [], 'results': {}, 'visible_result': None, 'step': 0}})
                db.add(run)
                await db.commit()
                local_id = run.local_run_id
            yield SimpleNamespace(app=app, client=client, settings=settings, user=user,
                token=token, local_id=local_id, fake=fake)
            assert not fake.calls


async def deliver(h, value, execution_id):
    """Post raw model text to the authenticated callback bound to one unchanged Agent run."""
    return (await h.client.post('/internal/nexus/agent-responses', headers=tool_auth(h.settings), json={
        'protocol_version': '1', 'application_id': 'personal_assistant', 'external_user_id': h.user,
        'run_id': 'repair-run', 'workflow_id': 'repair-workflow', 'response_id': execution_id,
        'execution_id': execution_id, 'content': value if isinstance(value, str) else json.dumps(value),
    })).json()


def proposed(operation, body, **fields):
    """Keep complete model-authored arguments and summary in each callback proposal."""
    return {'decision': 'call', 'call': {'operation_id': operation, 'body': body,
        'summary': '按用户请求保存，冲突时不擅自修改其他安排', **fields}}


@pytest.mark.parametrize(('value', 'hint'), [
    ('{"decision":', 'JSON'),
    ({'decision': 'answer'}, 'answer'),
    (proposed('create_todo', {'idempotency_key': 'missing-title'}), 'body.title'),
    (proposed('create_todo', {'title': 123, 'idempotency_key': 'bad-title'}), 'string'),
    (proposed('create_todo', {'title': {'private': 'secret-value'}, 'idempotency_key': 'private-title'}), 'body.title'),
    ({'decision': 'answer', 'answer': '请确认', 'cards': [{'title': '询问',
      'blocks': [{'type': 'paragraph', 'text': '选择时间'}]}]}, 'text'),
])
async def test_format_feedback_is_actionable_before_writes(repair_app, value, hint):
    """Malformed decisions, arguments and cards explain a repair and allow the same Agent to continue."""
    h = repair_app
    failed = await deliver(h, value, 'invalid-format')
    assert failed['execution_status'] == 'failed' and failed['disposition'] == 'continue', failed
    assert hint in failed['error']['message'], failed
    assert '未执行' in failed['error']['message'] or '未新增' in failed['error']['message'], failed
    assert 'secret-value' not in failed['error']['message']
    corrected = await deliver(h, {'decision': 'ask_user', 'question': '改到下午可以吗？',
        'choices': ['下午', '另选时间'], 'cards': [{'title': '选择时间', 'blocks': [
            {'type': 'text', 'text': '请选择合适时间。'}]}]}, 'correct-format')
    assert corrected['disposition'] == 'complete', corrected
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterDraft)) == 0
        assert await db.scalar(select(func.count()).select_from(ScheduleEvent)) == 0


async def test_agent_protocol_explains_repair_and_stop_conditions(repair_app):
    """Publish repair instructions within the existing protocol limit, without starting a Nexus run."""
    catalog = OperationCatalog(repair_app.app)
    configuration = workflow_configuration({'api_operations': [
        operation.model_description() for operation in catalog.search('新增日程 修改日程 冲突')
    ]})
    instructions = configuration['application_protocol_instructions']
    assert 'formal_write_status=not_applied' in instructions
    assert 'unknown/stop' in instructions and len(instructions) <= 16000


async def test_schedule_conflict_rolls_back_then_same_agent_can_correct(repair_app):
    """Keep earlier successes, expose the real conflict/draft, and correct only the failed formal write."""
    h = repair_app
    baseline = await h.client.post('/api/v1/schedules', headers=auth(h.token), json={
        'title': '原会议', 'starts_at': '2035-05-10T09:00:00+08:00',
        'ends_at': '2035-05-10T10:00:00+08:00', 'use_preferences': False,
        'idempotency_key': 'existing-schedule'})
    assert baseline.status_code == 201, baseline.text
    original = baseline.json()
    todo = await deliver(h, proposed('create_todo', {'title': '保留先前待办',
        'idempotency_key': 'prior-todo'}), 'prior-success')
    assert todo['execution_status'] == 'succeeded', todo
    bad = proposed('create_schedule', {'title': '新会议', 'starts_at': '2035-05-10T09:30:00+08:00',
        'ends_at': '2035-05-10T10:30:00+08:00', 'use_preferences': False, 'idempotency_key': 'new-meeting'})
    failed = await deliver(h, bad, 'schedule-conflict')
    assert failed['execution_status'] == 'failed' and failed['disposition'] == 'continue', failed
    assert failed['error']['code'] == 'conflict_detected'
    details = failed['result']['data']['error']['details']
    assert details['formal_write_status'] == 'not_applied'
    assert details['draft']['status'] == 'preview' and details['conflict']['has_conflict']
    assert original['schedule']['schedule_id'] in json.dumps(details['conflict'])
    assert failed['result']['executed_call']['summary'] == bad['call']['summary']
    assert (await deliver(h, bad, 'schedule-conflict')) == failed
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ScheduleEvent)) == 1
        assert await db.scalar(select(func.count()).select_from(Matter)) == 2  # original + prior todo
        draft = await db.get(MatterDraft, details['draft']['draft_id'])
        assert draft.status == 'preview' and draft.confirm_key is None
    bad['call']['body'].update(starts_at='2035-05-10T14:00:00+08:00', ends_at='2035-05-10T15:00:00+08:00')
    fixed = await deliver(h, bad, 'corrected-schedule')
    assert fixed['execution_status'] == 'succeeded', fixed
    saved = fixed['result']['data']
    edit = proposed('update_schedule', {'expected_matter_version': saved['matter']['version'],
        'starts_at': '2035-05-10T09:30:00+08:00', 'ends_at': '2035-05-10T10:30:00+08:00',
        'idempotency_key': 'edit-conflict'}, path_parameters={'schedule_id': saved['schedule']['schedule_id']})
    edited = await deliver(h, edit, 'edit-conflict')
    assert edited['execution_status'] == 'failed' and edited['disposition'] == 'continue', edited
    assert edited['result']['data']['error']['details']['target']['schedule_id'] == saved['schedule']['schedule_id']
    final = await deliver(h, {'decision': 'ask_user', 'question': '与原会议冲突，是否保留下午安排？',
        'choices': ['保留下午', '重新选时间']}, 'ask-about-conflict')
    assert final['disposition'] == 'complete'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(NexusRun)) == 1
        assert await db.scalar(select(func.count()).select_from(ScheduleEvent)) == 2
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 1
        row = await db.get(ScheduleEvent, saved['schedule']['schedule_id'])
        assert row.starts_at.hour == 6  # 14:00 +08:00, unchanged by the failed edit
        run = await db.get(NexusRun, h.local_id)
        assert len(run.context_json['api_flow']['receipts']) == 2


@pytest.mark.parametrize('failure', ['lost_response_after_commit', 'unproven_conflict', 'server_error'])
async def test_unknown_results_stop_without_reexecution(repair_app, monkeypatch, failure):
    """A lost response after a real save, or an unproven HTTP error, must never authorize another write."""
    h = repair_app
    dispatch = agent_responses.execute_call
    attempts = []

    async def uncertain(*args, **kwargs):
        """Either lose a committed route response or return an error with no rollback evidence."""
        attempts.append(1)
        if failure == 'lost_response_after_commit':
            await dispatch(*args, **kwargs)
            raise httpx.ReadTimeout('response lost')
        return {'status': 'failed', 'http_status': 409 if failure == 'unproven_conflict' else 500,
            'operation_id': 'create_schedule', 'data': {'error': {'code': 'conflict_detected',
                **({'details': {'formal_write_status': 'not_applied'}} if failure == 'server_error' else {})}}}

    monkeypatch.setattr(agent_responses, 'execute_call', uncertain)
    value = proposed('create_schedule', {'title': '结果待核对', 'starts_at': '2035-05-10T14:00:00+08:00',
        'use_preferences': False, 'idempotency_key': 'uncertain-save'})
    failed = await deliver(h, value, 'unknown-outcome')
    assert failed['execution_status'] == 'unknown' and failed['disposition'] == 'stop', failed
    assert (await deliver(h, value, 'unknown-outcome')) == failed
    assert (await deliver(h, value, 'new-execution-id'))['error']['code'] == 'processing_stopped'
    assert len(attempts) == 1
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ScheduleEvent)) == (failure == 'lost_response_after_commit')
