"""Focused compact-index/definition/callback checks, using isolated SQLite and no model calls.

Run: .venv/bin/python -m pytest tests/test_operation_index.py
The separate live comparison measures actual Nexus model rounds and provider usage.
"""
import json

import pytest
from sqlalchemy import func, select

from assistant_api.models import AgentResponseReceipt, MatterTodo, NexusRun
from assistant_api.services.api_operations import OperationCatalog
from assistant_api.services.operation_intake import model_context, workflow_configuration
from conftest import tool_auth


def index_flow():
    """Retain the user's full negative clauses without letting the backend select business intent."""
    return {'text': '新增待办，也不要修改日程。不要忘记保存。', 'query': '新增待办，也不要修改日程。',
        'now': '2035-05-10T00:00:00+00:00', 'timezone': 'Asia/Shanghai', 'history': [],
        'receipts': [], 'results': {}, 'step': 0, 'trigger_source': 'chat', 'request_key': 'index-test'}


async def index_run(h):
    """Bind the initial index and its supported definitions to one trusted local/remote run."""
    flow = index_flow()
    context = model_context(flow, OperationCatalog(h.app))
    context.update(caller_processed=True, api_flow=flow)
    async with h.app.state.session_factory() as db:
        row = NexusRun(user_id=h.user_a, nexus_run_id='index-run', workflow_execution_id='index-workflow',
            idempotency_key='index-task', status='running', request_text=flow['text'], context_json=context)
        db.add(row)
        await db.commit()
        return row.local_run_id


async def deliver(h, value, identity):
    """Deliver unmodified model decisions through the same authenticated callback used by Nexus."""
    response = await h.client.post('/internal/nexus/agent-responses', headers=tool_auth(h.settings), json={
        'protocol_version': '1', 'application_id': 'personal_assistant', 'external_user_id': h.user_a,
        'run_id': 'index-run', 'workflow_id': 'index-workflow', 'response_id': identity,
        'execution_id': identity, 'content': json.dumps(value, ensure_ascii=False)})
    assert response.status_code == 200, response.text
    return response.json()


async def test_bootstrap_has_compact_index_and_only_common_schedule_inputs(harness):
    """Long-tail operations remain indexed; only the resolver and two schedule inputs are loaded."""
    catalog = OperationCatalog(harness.app)
    flow = index_flow()
    context = model_context(flow, catalog)
    page = context['operation_index']
    assert len(page['items']) > 8 and page['total'] == len(catalog.operations)
    assert {catalog.operations[name].uri for name in ('create_todo', 'update_schedule')} <= {item['uri'] for item in page['items']}
    assert context['text'] == flow['text']
    assert [item['operation_id'] for item in context['api_operations']] == ['resolve_operation_definitions', 'list_schedules', 'create_schedule']
    assert all(set(item) == {'uri', 'summary', 'effect'} for item in page['items'])
    assert all('requestBody' not in item and 'components' not in item for item in page['items'])
    assert 'TodoCreateRequest' not in context['components']['schemas']
    assert 'enum' not in context['decision_schema']['$defs']['OperationCall']['properties']['operation_id']
    protocol = workflow_configuration(context)['application_protocol_instructions']
    assert 'resolve_operation_definitions' in protocol and 'uri' in protocol


async def test_index_pagination_does_not_hide_operations_or_interpret_negation(harness):
    """Read all index pages and retain edit descriptions even when a search includes a negation."""
    catalog = OperationCatalog(harness.app)
    identities = []
    offset = 0
    while True:
        page = catalog.index_page(offset=offset, limit=11)
        identities.extend(item['uri'] for item in page['items'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert len(identities) == len(set(identities)) == len(catalog.operations)
    assert 'update_schedule' in {item.operation_id for item in catalog.search('不要修改日程', limit=128)}


async def test_resolver_then_business_call_reuses_run_and_definitions(harness):
    """One lookup survives argument repair and a second distinct write without needing another lookup."""
    h = harness
    local_id = await index_run(h)
    create = {'decision': 'call', 'call': {'operation_id': 'create_todo', 'summary': '保存新待办',
        'body': {'title': '索引验证待办', 'idempotency_key': 'index-todo'}}}
    missing = await deliver(h, create, 'before-definition')
    assert missing['execution_status'] == 'failed' and missing['error']['code'] == 'operation_definition_required'
    uri = OperationCatalog(h.app).operations['create_todo'].uri
    lookup = {'decision': 'call', 'call': {'operation_id': 'resolve_operation_definitions', 'body': {'uris': [uri]}}}
    loaded = await deliver(h, lookup, 'resolve-index')
    assert loaded['execution_status'] == 'succeeded', loaded
    data = loaded['result']['data']
    assert [op['operation_id'] for op in data['api_operations']] == ['create_todo']
    assert 'TodoCreateRequest' in data['components']['schemas']
    assert (await deliver(h, lookup, 'resolve-index')) == loaded
    repeated = await deliver(h, lookup, 'resolve-again')
    assert repeated['result']['data']['api_operations'] == []
    assert repeated['result']['data']['components']['schemas'] == {}
    assert repeated['result']['data']['already_loaded'] == [uri]
    invalid = json.loads(json.dumps(create))
    invalid['call']['body']['title'] = 123
    rejected = await deliver(h, invalid, 'invalid-argument')
    assert rejected['error']['code'] == 'invalid_parameters' and rejected['disposition'] == 'continue'
    result = await deliver(h, create, 'actual-create')
    assert result['execution_status'] == 'succeeded', result
    assert result['result']['data']['todo']['title'] == '索引验证待办'
    another = json.loads(json.dumps(create))
    another['call']['body'].update(title='第二条不同待办', idempotency_key='index-second-todo')
    assert (await deliver(h, another, 'second-create'))['execution_status'] == 'succeeded'
    final = await deliver(h, {'decision': 'answer', 'answer': '已保存索引验证待办。'}, 'final-answer')
    assert final['disposition'] == 'complete'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(NexusRun)) == 1
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 2
        row = await db.get(NexusRun, local_id)
        assert 'create_todo' in row.context_json['api_flow']['offered_operations']


@pytest.mark.parametrize('decision', [
    {'decision': 'answer', 'answer': '你好。'},
    {'decision': 'ask_user', 'question': '希望安排哪一天？', 'choices': ['明天', '后天']},
])
async def test_answer_and_clarification_need_no_lookup(harness, decision):
    """A new Agent can answer or ask immediately with zero definition tool requests."""
    h = harness
    local_id = await index_run(h)
    assert (await deliver(h, decision, 'no-lookup'))['disposition'] == 'complete'
    async with h.app.state.session_factory() as db:
        receipts = list(await db.scalars(select(AgentResponseReceipt).where(AgentResponseReceipt.local_run_id == local_id)))
        assert len(receipts) == 1 and receipts[0].executed_call_json is None
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0
    assert not h.fake.calls


async def test_new_agent_does_not_inherit_definition_cache(harness):
    """A new initial message must carry its own resolver schema and cannot borrow another Agent's flags."""
    flow = index_flow()
    flow['offered_operations'] = ['resolve_operation_definitions', 'create_todo']
    flow['provided_schema_names'] = ['OperationDefinitionsRequest', 'TodoCreateRequest']
    context = model_context(flow, OperationCatalog(harness.app))
    assert [operation['operation_id'] for operation in context['api_operations']] == ['resolve_operation_definitions', 'list_schedules', 'create_schedule']
    assert 'OperationDefinitionsRequest' in context['components']['schemas']
    assert 'TodoCreateRequest' not in context['components']['schemas']
    assert flow['offered_operations'] == ['resolve_operation_definitions', 'list_schedules', 'create_schedule']


async def test_loaded_flag_without_this_agents_definition_does_not_authorize_call(harness):
    """Only initial-message or successful same-run definition results count as available to this Agent."""
    h = harness
    local_id = await index_run(h)
    async with h.app.state.session_factory() as db:
        root = await db.get(NexusRun, local_id)
        context = json.loads(json.dumps(root.context_json))
        context['api_flow']['offered_operations'].append('create_todo')
        root.context_json = context
        await db.commit()
    response = await deliver(h, {'decision': 'call', 'call': {'operation_id': 'create_todo',
        'body': {'title': '没有实际定义', 'idempotency_key': 'cache-not-a-message'}}}, 'cache-only')
    assert response['execution_status'] == 'failed' and response['error']['code'] == 'operation_definition_required'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0


@pytest.mark.parametrize('changed_part', ['description', 'shared_schema', 'shared_error_schema'])
async def test_stale_definition_requires_only_its_own_reload(harness, changed_part):
    """Changed published definitions reject before dispatch; reloading delivers changed shared schemas too."""
    h = harness
    await index_run(h)
    uri = OperationCatalog(h.app).operations['create_todo'].uri
    lookup = {'decision': 'call', 'call': {'operation_id': 'resolve_operation_definitions', 'body': {'uris': [uri]}}}
    assert (await deliver(h, lookup, 'load-old'))['execution_status'] == 'succeeded'
    document = json.loads(json.dumps(h.app.openapi()))
    if changed_part == 'description':
        document['paths']['/api/v1/todos']['post']['description'] += ' 新发布的接口说明。'
    elif changed_part == 'shared_schema':
        document['components']['schemas']['TodoCreateRequest']['properties']['title']['description'] = '新发布的字段说明。'
    else:
        document['components']['schemas']['ErrorEnvelope']['description'] = '更新共享错误结构；解析工具仍可用于取得新定义。'
    h.app.openapi_schema = document
    create = {'decision': 'call', 'call': {'operation_id': 'create_todo',
        'body': {'title': '更新定义后保存', 'idempotency_key': 'definition-updated'}}}
    stale = await deliver(h, create, 'stale-definition')
    assert stale['execution_status'] == 'failed' and stale['disposition'] == 'continue'
    assert stale['error']['code'] == 'operation_definition_stale'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0
    loaded = await deliver(h, lookup, 'load-current')
    data = loaded['result']['data']
    assert data['updated_uris'] == [uri] and not data['already_loaded']
    assert data['api_operations'][0]['operation_id'] == 'create_todo'
    if changed_part == 'shared_schema':
        assert data['components']['schemas']['TodoCreateRequest']['properties']['title']['description'] == '新发布的字段说明。'
    assert (await deliver(h, create, 'save-after-reload'))['execution_status'] == 'succeeded'


@pytest.mark.parametrize('uri', ['https://example.invalid/private', 'assistant://operations/not-published'])
async def test_resolver_rejects_unpublished_uris_without_fetching(harness, uri):
    """A URI is a local catalogue identifier, never an arbitrary network fetch or an authorization grant."""
    h = harness
    await index_run(h)
    response = await deliver(h, {'decision': 'call', 'call': {
        'operation_id': 'resolve_operation_definitions', 'body': {'uris': [uri]}}}, 'invalid-uri')
    assert response['execution_status'] == 'failed' and response['disposition'] == 'continue', response
    assert response['error']['code'] == 'operation_uri_invalid'
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0
