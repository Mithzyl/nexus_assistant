"""Focused card schema, binding, display and callback checks; no live model or existing database.

Run .venv/bin/python -m pytest tests/test_card_input_contract.py
Use the same published input schema and real callback with isolated SQLite.
"""
import copy
import json

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from assistant_api.models import AgentResponseReceipt, MatterTodo, NexusRun
from assistant_api.services import operation_intake, response_cards
from conftest import auth, tool_auth


def card(value, *, kind='result', format='text'):
    """Build a single input field without copying any expected formatter implementation."""
    return {'kind': kind, 'title': '待办', 'blocks': [{'type': 'fields', 'items': [
        {'label': '状态', 'value': value, 'format': format}]}]}


def receipt(data):
    """Represent a successful business response, independent from its optional presentation."""
    return {'status': 'succeeded', 'http_status': 201, 'operation_id': 'create_todo', 'data': data}


def test_result_literals_are_rejected_by_both_schema_and_runtime():
    """The exact historical failure is invalid in the model's schema and reports its field path locally."""
    value = card('未完成')
    assert not Draft202012Validator(response_cards.presentation_schema()).is_valid(value)
    with pytest.raises(ValueError, match=r'cards\.0\.blocks\.0\.items\.0\.value'):
        response_cards.prepare_cards([value], receipt({'todo': {'status': 'open'}}))


@pytest.mark.parametrize(('format', 'state', 'label'), [
    ('todo_status', 'open', '未完成'), ('todo_status', 'done', '已完成'),
    ('schedule_status', 'cancelled', '已取消'), ('matter_status', 'waiting', '等待中'),
    ('reminder_status', 'due', '已到提醒时间'), ('todo_status', 'future_state', 'future_state'),
])
def test_status_formats_bind_actual_values_without_changing_them(format, state, label):
    """Use an explicit business domain; unknown codes remain visible and never become successful states."""
    value = card({'$result': '/state'}, format=format)
    data = receipt({'state': state})
    original = copy.deepcopy((value, data))
    assert Draft202012Validator(response_cards.presentation_schema()).is_valid(value)
    result = response_cards.prepare_cards([value], data)[0]
    field = result['blocks'][0]['items'][0]
    assert field['value'] == label and field['format'] == 'text'
    assert result['status'] == 'completed'
    assert (value, data) == original


def test_status_format_cannot_translate_an_unbound_model_constant():
    """A display formatter must not turn a model-authored status into an apparent business fact."""
    value = card('open', kind='info', format='todo_status')
    assert not Draft202012Validator(response_cards.presentation_schema()).is_valid(value)
    with pytest.raises(ValueError):
        response_cards.prepare_cards([value], receipt({'state': 'open'}))


def test_image_and_timeline_bindings_match_the_published_schema():
    """Result images, collection templates and item fields share the same reference rules as scalar fields."""
    value = {'kind': 'result', 'title': '查询结果', 'blocks': [
        {'type': 'image', 'src': {'$result': '/image'}, 'alt': '结果图片'},
        {'type': 'timeline', 'items': {'$each': '/items', 'template': {
            'title': {'$item': '/title'}, 'fields': [
                {'label': '状态', 'value': {'$item': '/status'}, 'format': 'todo_status'}],
            'image': {'src': {'$item': '/image'}, 'alt': '条目图片'}}}},
    ]}
    validator = Draft202012Validator(response_cards.presentation_schema())
    assert validator.is_valid(value), list(validator.iter_errors(value))
    data = receipt({'image': 'https://example.test/main.png', 'items': [
        {'title': '整理资料', 'status': 'open', 'image': 'https://example.test/item.png'}]})
    result = response_cards.prepare_cards([value], data)[0]
    assert result['blocks'][0]['src'] == 'https://example.test/main.png'
    assert result['blocks'][1]['items'][0]['fields'][0]['value'] == '未完成'
    invalid = copy.deepcopy(value)
    invalid['blocks'][0]['src'] = 'https://example.test/main.png'
    assert not validator.is_valid(invalid)
    with pytest.raises(ValueError):
        response_cards.prepare_cards([invalid], data)


@pytest.mark.parametrize('kind', ['info', 'plan'])
def test_advice_literals_remain_supported(kind):
    """Explanation and unsaved-plan fields can contain text without pretending to be saved result values."""
    value = card('建议先整理资料', kind=kind)
    assert Draft202012Validator(response_cards.presentation_schema()).is_valid(value)
    assert response_cards.prepare_cards([value], None)[0]['blocks'][0]['items'][0]['value'] == '建议先整理资料'


def test_legacy_top_level_status_is_ignored_instead_of_claiming_completion():
    """Retain old input compatibility while deriving status from the actual card purpose/result."""
    value = card('建议先整理资料', kind='plan')
    value['status'] = 'legacy-model-status'
    assert Draft202012Validator(response_cards.presentation_schema()).is_valid(value)
    assert response_cards.prepare_cards([value], None)[0]['status'] == 'suggestion'


def test_existing_literal_display_formats_remain_consistent_with_input_schema():
    """Keep supported advice arrays/seconds while rejecting wrong literal types before resolution."""
    validator = Draft202012Validator(response_cards.presentation_schema())
    for value, format, expected in [([1, 2], 'weekdays', '周一、周二'),
                                     (['资料', '快递'], 'list', '资料、快递'),
                                     (90, 'duration_seconds', '1分钟30秒')]:
        authored = card(value, kind='info', format=format)
        assert validator.is_valid(authored)
        assert response_cards.prepare_cards([authored], None)[0]['blocks'][0]['items'][0]['value'] == expected
    for value, format in [('90', 'duration_seconds'), ([0], 'weekdays'), (['资料'], 'text')]:
        authored = card(value, kind='info', format=format)
        assert not validator.is_valid(authored)
        with pytest.raises(ValueError):
            response_cards.prepare_cards([authored], None)


def test_seconds_and_status_display_rules_are_visible_in_schema_and_prompt():
    """The shared prompt advertises actual results, optional cards and the supported display formats."""
    protocol = operation_intake.workflow_configuration({'api_operations': []})['application_protocol_instructions']
    for obsolete in ('执行结果仅包含状态', '不会把原业务值交给你', '本地解析，不把正文交给模型',
                     '成功响应已包含实际结果时直接回答并绑定卡片', '请用户查看结果卡片'):
        assert obsolete not in protocol
    assert 'todo_status' in protocol and 'cards=[]' in protocol
    value = card({'$result': '/reminder_offset_seconds'}, format='relative_seconds')
    assert Draft202012Validator(response_cards.presentation_schema()).is_valid(value)
    assert response_cards.prepare_cards([value], receipt({'reminder_offset_seconds': -720}))[0]['blocks'][0]['items'][0]['value'] == '提前12分钟'


async def callback(h, value, identity, remote='card-run'):
    """Submit a raw model-shaped response to the authenticated callback without invoking any model."""
    response = await h.client.post('/internal/nexus/agent-responses', headers=tool_auth(h.settings), json={
        'protocol_version': '1', 'application_id': 'personal_assistant', 'external_user_id': h.user_a,
        'run_id': remote, 'workflow_id': 'card-workflow', 'response_id': identity,
        'execution_id': identity, 'content': json.dumps(value, ensure_ascii=False)})
    assert response.status_code == 200, response.text
    return response.json()


async def test_saved_todo_error_repair_and_projection_without_repeating_business(harness):
    """A real saved todo survives rejected card input; corrected display projects Chinese with no second write."""
    h = harness
    created = await h.client.post('/api/v1/todos', headers=auth(h.token_a), json={
        'title': '整理路线资料', 'idempotency_key': 'card-actual-todo'})
    assert created.status_code == 201, created.text
    actual = receipt(created.json())
    async with h.app.state.session_factory() as db:
        root = NexusRun(user_id=h.user_a, nexus_run_id='card-run', workflow_execution_id='card-workflow',
            idempotency_key='card-task', request_text='创建待办整理路线资料，只回复标题。',
            status='running', context_json={'caller_processed': True,
                'api_flow': {'receipts': [actual], 'results': {'0': actual}, 'visible_result': actual,
                             'step': 1, 'offered_operations': []}})
        db.add(root)
        await db.commit()
        local_id = root.local_run_id
    invalid = {'decision': 'answer', 'answer': '已保存整理路线资料。', 'cards': [card('未完成')]}
    failed = await callback(h, invalid, 'bad-card')
    assert failed['execution_status'] == 'failed' and failed['disposition'] == 'continue'
    assert 'cards.0.blocks.0.items.0.value' in failed['error']['message']
    corrected = {**invalid, 'cards': [card({'$result': '/todo/status'}, format='todo_status')]}
    assert (await callback(h, corrected, 'fixed-card'))['disposition'] == 'complete'
    async with h.app.state.session_factory() as db:
        root = await db.get(NexusRun, local_id)
        root.status = 'succeeded'
        root.result_json = {'final_text': json.dumps(corrected)}
        await db.commit()
        projection = operation_intake.response_for(root)
        assert projection['pending_context']['cards'][0]['blocks'][0]['items'][0]['value'] == '未完成'
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 1
        assert (await db.get(MatterTodo, created.json()['todo']['todo_id'])).status == 'open'
        saved = await db.get(AgentResponseReceipt, 'bad-card')
        assert json.loads(saved.request_json['content']) == invalid
    assert not h.fake.calls


@pytest.mark.parametrize('decision', [
    {'decision': 'answer', 'answer': '已保存整理路线资料。', 'cards': []},
    {'decision': 'ask_user', 'question': '需要安排哪一天？', 'choices': ['明天', '后天'], 'cards': []},
])
def test_text_only_answers_and_questions_never_get_automatic_cards(decision):
    """Preserve text and choices while leaving optional cards empty; no user-intent regex is used."""
    from assistant_api.services.agent_responses import final_projection
    projected = final_projection(json.dumps(decision), {'receipts': [], 'results': {}, 'visible_result': None})
    assert projected['pending_context']['cards'] == []
    assert (projected['answer'] or projected['clarify']) == (decision.get('answer') or decision.get('question'))
    if 'choices' in decision:
        assert projected['pending_context']['choices'] == decision['choices']
