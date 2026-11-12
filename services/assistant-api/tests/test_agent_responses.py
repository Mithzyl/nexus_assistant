"""Caller body acceptance using real App routes and isolated business storage."""
import json
import pytest
from sqlalchemy import func, select
from assistant_api.models import MatterTodo, NexusRun
from assistant_api.services import operation_intake
from conftest import tool_auth


async def prepare_run(h):
    """Persist a trusted intake and selected actual OpenAPI definitions."""
    from assistant_api.services.api_operations import OperationCatalog
    flow = {'text': '创建待办下班打卡，2030年11月5日18:10提醒', 'query': '创建待办',
            'now': '2030-11-05T09:59:00+00:00', 'timezone': 'Asia/Shanghai',
            'history': [], 'receipts': [], 'step': 0, 'state': 'planning',
            'trigger_source': 'chat', 'request_key': 'body-test-request'}
    context = operation_intake.model_context(flow, OperationCatalog(h.app))
    context.update(caller_processed=True, api_flow=flow)
    async with h.app.state.session_factory() as db:
        row = NexusRun(user_id=h.user_a, nexus_run_id='body-run', workflow_execution_id='body-workflow',
                       idempotency_key='body-test-request', request_text=flow['text'],
                       status='running', purpose='matter_plan', context_json=context)
        db.add(row)
        await db.commit()
        return row.local_run_id


def proposal():
    """Return original create parameters and summary."""
    return {'decision': 'call', 'call': {'operation_id': 'create_structured_draft',
            'body': {'title': '下班打卡', 'todos': [{'title': '下班打卡', 'reminder_at': '2030-11-05T18:10:00+08:00'}],
                     'idempotency_key': 'model-original-key'}, 'summary': '新增待办并保存18:10提醒'}}


def envelope(h, value=None, execution_id='execution-1'):
    """Bind complete raw text to one existing run, workflow and owner."""
    return {'protocol_version': '1', 'application_id': 'personal_assistant',
            'external_user_id': h.user_a, 'run_id': 'body-run', 'workflow_id': 'body-workflow',
            'response_id': execution_id + '-response', 'execution_id': execution_id,
            'content': json.dumps(value or proposal(), ensure_ascii=False)}


async def send(h, payload):
    """Deliver the Nexus envelope through the real router."""
    return await h.client.post('/internal/nexus/agent-responses', json=payload, headers=tool_auth(h.settings))


@pytest.mark.parametrize('change', ['owner', 'workflow', 'application', 'cancelled'])
async def test_identity_and_cancellation_reject_before_business_dispatch(harness, change):
    """Only the bound active user and workflow can execute new bodies."""
    h = harness
    local_id = await prepare_run(h)
    payload = envelope(h)
    if change == 'owner': payload['external_user_id'] = h.user_b
    elif change == 'workflow': payload['workflow_id'] = 'other-workflow'
    elif change == 'application': payload['application_id'] = 'other-app'
    else:
        async with h.app.state.session_factory() as db:
            row = await db.get(NexusRun, local_id)
            row.status = 'cancelled'
            await db.commit()
    response = await send(h, payload)
    assert response.status_code in {200, 403, 404}, response.text
    if response.status_code == 200:
        assert response.json()['execution_status'] == 'rejected'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0


async def test_complete_waits_for_reviewed_terminal_result(harness):
    """Complete has exactly three fields and does not publish an unreviewed answer."""
    h = harness
    local_id = await prepare_run(h)
    value = {'decision': 'answer', 'answer': '请提供明确时间。'}
    result = await send(h, envelope(h, value))
    assert result.status_code == 200, result.text
    assert result.json() == {'protocol_version': '1', 'execution_id': 'execution-1', 'disposition': 'complete'}
    async with h.app.state.session_factory() as db:
        row = await db.get(NexusRun, local_id)
        assert operation_intake.response_for(row)['answer'] is None
        row.status = 'succeeded'
        row.result_json = {'final_text': json.dumps(value)}
        await db.commit()
        assert operation_intake.response_for(row)['answer'] == value['answer']


async def test_dispatching_receipt_is_never_reexecuted_after_restart(harness):
    """A durable dispatch claim without a response remains unknown under any new ID."""
    from assistant_api.models import AgentResponseReceipt
    h = harness
    local_id = await prepare_run(h)
    payload = envelope(h)
    async with h.app.state.session_factory() as db:
        row = await db.get(NexusRun, local_id)
        row.context_json = {**row.context_json, 'body_processing_execution_id': payload['execution_id']}
        db.add(AgentResponseReceipt(execution_id=payload['execution_id'], local_run_id=local_id,
            response_id=payload['response_id'], request_json=payload, status='dispatching'))
        await db.commit()
    assert (await send(h, payload)).json()['execution_status'] == 'unknown'
    assert (await send(h, envelope(h, execution_id='new-execution'))).json()['execution_status'] == 'unknown'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0


async def test_callback_rejects_oversized_envelope_before_dispatch(harness):
    """The HTTP request bound is measured in bytes before JSON validation or business writes."""
    h = harness
    await prepare_run(h)
    payload = envelope(h)
    payload['content'] = '字' * 400000
    result = await send(h, payload)
    assert result.status_code == 413
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0


async def test_additive_migration_preserves_existing_app_rows(tmp_path):
    """Upgrade an old App schema using the actual migrator and retain existing business rows."""
    import importlib.util
    from pathlib import Path
    from sqlalchemy import text
    from assistant_api.infrastructure.database import create_engine_and_factory
    from assistant_api.models import Base, AgentResponseReceipt, User
    path = tmp_path / 'pre-caller.db'
    engine, factory = create_engine_and_factory(f'sqlite+aiosqlite:///{path}')
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[table for table in Base.metadata.sorted_tables if table.name != 'agent_response_receipts']))
    async with factory() as db:
        db.add(User(user_id='preserved-user', email='preserved@example.test', display_name='保留用户', password_verifier='synthetic-verifier', status='active'))
        await db.commit()
    spec = importlib.util.spec_from_file_location('caller_migrator', Path(__file__).resolve().parents[1] / 'scripts/migrate_sqlite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    await module.upgrade(f'sqlite+aiosqlite:///{path}')
    async with factory() as db:
        assert await db.get(User, 'preserved-user') is not None
        assert await db.scalar(select(func.count()).select_from(AgentResponseReceipt)) == 0
        assert await db.scalar(text("SELECT version FROM schema_migrations WHERE version='0026_agent_response_receipts'")) == '0026_agent_response_receipts'
    await engine.dispose()
