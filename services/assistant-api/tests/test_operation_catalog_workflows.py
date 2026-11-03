"""Focused checks for published business workflows; temporary DB and fixed Nexus JSON only."""
import json
import pytest
from sqlalchemy import select
from conftest import auth
from test_api_operation_flow import fixed_decisions
from assistant_api.models import Matter, MatterTodo, ScheduleEvent
from assistant_api.services.api_operations import OperationCatalog
from assistant_api.services import api_operations, response_cards


@pytest.mark.asyncio
async def test_create_todo_from_input_saves_existing_transaction_and_real_result(harness):
    """A new user creates a real todo without first creating an orphan matter or approving twice."""
    h = harness
    fixed_decisions(h, [
        {'decision': 'call', 'call': {'operation_id': 'create_structured_draft', 'body': {'idempotency_key': 'new-todo-preview-001', 'title': '弄清楚优惠销售品', 'todos': [{'title': '弄清楚优惠销售品'}]}, 'summary': '准备待办'}},
        {'decision': 'call', 'call': {'operation_id': 'confirm_draft_api_v1_matters_drafts__draft_id__confirm_post', 'path_parameters': {'draft_id': {'$result': '/draft_id'}}, 'body': {'expected_version': {'$result': '/version'}}, 'summary': '保存待办'}},
        {'decision': 'call', 'call': {'operation_id': 'get_matter_api_v1_matters__matter_id__get', 'path_parameters': {'matter_id': {'$result': '/matter/matter_id'}}, 'summary': '读取已保存待办'}},
        {'decision': 'answer', 'answer': '待办加好了。', 'cards': [{'kind': 'result', 'title': '弄清楚优惠销售品', 'blocks': [{'type': 'fields', 'items': [{'label': '待办', 'value': {'$result': '/todos/0/title'}}]}]}]},
    ])
    result = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_a), json={'text': '新增一个弄清楚优惠销售品的待办', 'idempotency_key': 'new-user-todo-flow-001'})
    assert result.status_code == 200, result.text
    assert result.json()['outcome'] == 'answer'
    assert result.json()['pending_context']['cards'][0]['blocks'][0]['items'][0]['value'] == '弄清楚优惠销售品'
    async with h.app.state.session_factory() as db:
        rows = list(await db.scalars(select(MatterTodo).where(MatterTodo.title == '弄清楚优惠销售品')))
        assert len(rows) == 1
        assert (await db.get(Matter, rows[0].matter_id)).title == '弄清楚优惠销售品'
        assert not list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.matter_id == rows[0].matter_id)))
    prompts = [json.loads(c['body'])['user_request'] for c in h.fake.calls if c['path'] == '/api/v1/app/runs']
    contexts = [json.loads(prompt.rsplit('规则）：\n', 1)[1]) for prompt in prompts]
    assert len({context['idempotency_key'] for context in contexts}) == len(contexts)
    assert all(len(context['idempotency_key']) <= 128 for context in contexts)
    assert 'next_operations' in prompts[0]
    assert 'confirm_draft_api_v1_matters_drafts__draft_id__confirm_post' in prompts[0]


@pytest.mark.asyncio
async def test_append_draft_keeps_original_records_checks_versions_and_replays(harness):
    """Appending a todo reuses the selected matter and rejects stale, foreign and reused requests."""
    h = harness
    existing = await h.client.post('/api/v1/matters', headers=auth(h.token_a), json={'title': '已有事项'})
    mid, version = existing.json()['matter_id'], existing.json()['version']
    body = {'idempotency_key': 'append-todo-preview-001', 'matter_id': mid, 'expected_matter_version': version, 'todos': [{'title': '新增待办'}]}
    denied = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_b), json=body)
    assert denied.status_code == 404, denied.text
    stale = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json={**body, 'expected_matter_version': version + 1})
    assert stale.status_code == 409, stale.text
    draft = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json=body)
    assert draft.status_code == 201, draft.text
    repeated = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json=body)
    assert repeated.json()['draft_id'] == draft.json()['draft_id']
    reused = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json={**body, 'todos': [{'title': '另一个待办'}]})
    assert reused.status_code == 409
    saved = await h.client.post(f"/api/v1/matters/drafts/{draft.json()['draft_id']}/confirm", headers={**auth(h.token_a), 'Idempotency-Key': 'append-todo-save-001'}, json={'expected_version': draft.json()['version']})
    assert saved.status_code == 200, saved.text
    detail = await h.client.get(f'/api/v1/matters/{mid}', headers=auth(h.token_a))
    assert detail.json()['title'] == '已有事项'
    assert [t['title'] for t in detail.json()['todos']] == ['新增待办']
    assert detail.json()['schedules'] == []


def test_openapi_constraints_and_declared_workflow_links_are_executable(harness):
    """Conditional inputs and preparatory effects survive retrieval before HTTP dispatch."""
    catalog = OperationCatalog(harness.app)
    by_path = {(op.method, op.path): op for op in catalog.operations.values()}
    edit = by_path['POST', '/api/v1/matters/{matter_id}/drafts']
    for target in ({}, {'schedule_id': 'a', 'todo_id': 'b'}):
        with pytest.raises(ValueError):
            catalog.validate_call({'operation_id': edit.operation_id, 'path_parameters': {'matter_id': 'm'}, 'body': {'idempotency_key': 'target-test-001', **target}})
    create = by_path['POST', '/api/v1/matters/drafts']
    for body in ({'idempotency_key': 'create-test-001'}, {'idempotency_key': 'create-test-001', 'matter_id': 'm'}, {'idempotency_key': 'create-test-001', 'title': '新建', 'todos': [{'title': 'todo', 'todo_id': 'original'}]}):
        with pytest.raises(ValueError):
            catalog.validate_call({'operation_id': create.operation_id, 'body': body})
    assert by_path['POST', '/api/v1/commute/places/search'].effect == 'read'
    assert by_path['POST', '/api/v1/matters/drafts/{draft_id}/apply-option'].effect == 'preview'
    assert not any('/push-devices' in op.path or '/commute/bark' in op.path or op.path == '/api/v1/content' for op in catalog.operations.values())
    assert create.definition['next_operations']
    selected = catalog.search('新增待办')
    assert selected[0] == create
    assert any(op.path.endswith('/confirm') for op in selected)
    for op in catalog.operations.values():
        assert op.definition.get('description'), op.path
        for next_op in op.definition.get('next_operations', []):
            assert next_op['operation_id'] in catalog.operations


def test_runtime_only_constraints_are_published_before_dispatch(harness):
    """Reject known conditional-schema mistakes without making business requests."""
    catalog = OperationCatalog(harness.app)
    by_path = {(op.method, op.path): op for op in catalog.operations.values()}
    estimate = by_path['POST', '/api/v1/commute/estimate']
    with pytest.raises(ValueError):
        catalog.validate_call({'operation_id': estimate.operation_id, 'body': {'destination': {'latitude': 24, 'longitude': 109}, 'depart_at': '2030-01-01T09:00:00+08:00'}})
    follow = by_path['POST', '/api/v1/follow-ups']
    with pytest.raises(ValueError):
        catalog.validate_call({'operation_id': follow.operation_id, 'body': {'idempotency_key': 'follow-constraint-001', 'matter_id': 'm', 'title': '跟进', 'instruction': '到时检查', 'references': [{'name': 'matter', 'operation_id': 'get_matter_api_v1_matters__matter_id__get'}], 'trigger': {'kind': 'time'}}})
    plan = by_path['PUT', '/api/v1/commute-plans/{plan_id}']
    assert next(p for p in plan.definition['parameters'] if p['name'] == 'If-Match')['required']
    receipt = api_operations.model_receipt({'status': 'succeeded', 'http_status': 200, 'data': {'applied': False, 'private': 'not-model-context'}})
    assert receipt['applied'] is False
    assert 'not-model-context' not in json.dumps(receipt)
    with pytest.raises(ValueError):
        response_cards.prepare_cards([{'kind': 'result', 'title': '保存完成', 'blocks': []}], {'status': 'succeeded', 'http_status': 200, 'data': {'applied': False}})


@pytest.mark.asyncio
async def test_full_update_uses_local_fields_and_real_revision_header(harness):
    """A full PUT retains private existing fields and uses the actual revision without model disclosure."""
    h = harness
    original = {'name': '原通勤', 'timezone': 'Asia/Shanghai', 'weekdays': [1, 2, 3, 4, 5], 'arrival_local_time': '09:00', 'check_local_time': '07:00', 'transport_mode': 'driving', 'origin': {'label': '私有出发点', 'latitude': 24.3, 'longitude': 109.4}, 'destination': {'label': '私有目的地', 'latitude': 24.4, 'longitude': 109.5}}
    created = await h.client.post('/api/v1/commute-plans', headers=auth(h.token_a), json=original)
    assert created.status_code == 201, created.text
    plan_id = created.json()['id']
    fixed_decisions(h, [
        {'decision': 'call', 'call': {'operation_id': 'get_commute_plan_api_v1_commute_plans__plan_id__get', 'path_parameters': {'plan_id': plan_id}, 'summary': '读取原通勤计划'}},
        {'decision': 'call', 'call': {'operation_id': 'update_commute_plan_api_v1_commute_plans__plan_id__put', 'path_parameters': {'plan_id': plan_id}, 'headers': {'If-Match': {'$result': '/revision', 'as': 'string'}}, 'body': {'$result': '', 'pick': list(original), 'overrides': {'name': '新通勤'}}, 'summary': '修改原通勤名称'}},
        {'decision': 'answer', 'answer': '名称改好了。'},
    ])
    result = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_a), json={'text': '读取通勤计划当前配置和修订号，修改原通勤计划名称为新通勤', 'idempotency_key': 'full-plan-edit-001'})
    assert result.status_code == 200, result.text
    saved = await h.client.get(f'/api/v1/commute-plans/{plan_id}', headers=auth(h.token_a))
    assert saved.json()['name'] == '新通勤' and saved.json()['revision'] == 2
    assert saved.json()['origin'] == original['origin']
    prompts = '\n'.join(c['body'] for c in h.fake.calls if c['path'] == '/api/v1/app/runs')
    assert '私有出发点' not in prompts and '私有目的地' not in prompts


@pytest.mark.asyncio
async def test_commute_entry_reuses_existing_draft_without_formal_records(harness):
    """An explicit commute request enters the existing quote workflow and replays the same draft."""
    h = harness
    body = {'idempotency_key': 'commute-create-draft-001', 'origin_query': '用户指定起点', 'destination_query': '用户指定终点', 'city': '柳州', 'arrival_local_time': '09:00'}
    response = await h.client.post('/api/v1/matters/commute-drafts', headers=auth(h.token_a), json=body)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft['payload']['commute']['intent']['arrival_local_time'] == '09:00'
    assert draft['status'] == 'preview'
    assert draft['matter_id'] is None
    replay = await h.client.post('/api/v1/matters/commute-drafts', headers=auth(h.token_a), json=body)
    assert replay.json()['draft_id'] == draft['draft_id']
    catalog = OperationCatalog(h.app)
    op = catalog.operations['create_commute_draft']
    assert op.effect == 'preview'
    assert any(link['operation_id'].startswith('quote_commute_') for link in op.definition['next_operations'])


@pytest.mark.asyncio
async def test_last_allowed_operation_can_still_return_ai_answer(harness, monkeypatch):
    """The final reply is read after the operation budget is used, without dispatching another call."""
    from assistant_api.services import operation_intake
    monkeypatch.setattr(operation_intake, 'MAX_STEPS', 1)
    h = harness
    fixed_decisions(h, [
        {'decision': 'call', 'call': {'operation_id': 'list_matters_api_v1_matters_get', 'summary': '查询事项列表'}},
        {'decision': 'answer', 'answer': '已经查到了。'},
    ])
    response = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_a), json={'text': '查询事项列表', 'idempotency_key': 'last-allowed-operation-001'})
    assert response.status_code == 200, response.text
    assert response.json()['answer'] == '已经查到了。'
    assert response.json()['outcome'] == 'answer'


def test_optional_request_body_can_be_omitted(harness):
    """An optional typed body is absent rather than replaced with an invalid empty object."""
    catalog = OperationCatalog(harness.app)
    op = next(op for op in catalog.operations.values() if op.path == '/api/v1/commute-plans/{plan_id}/checks' and op.method == 'POST')
    _, call = catalog.validate_call({'operation_id': op.operation_id, 'path_parameters': {'plan_id': 'original-plan'}})
    assert call.body is None
    with pytest.raises(ValueError):
        catalog.validate_call({'operation_id': op.operation_id, 'path_parameters': {'plan_id': 'original-plan'}, 'body': {}})
