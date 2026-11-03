"""Focused checks for explicit-intent execution and model-authored embedded cards.

Uses the existing temporary SQLite/ASGI harness and fixed Nexus outputs. No real
business data or external provider is used by these checks.
"""
import asyncio
import pytest

from assistant_api.services import response_cards
from conftest import auth
from test_api_operation_flow import add_future_interface, fixed_decisions, widget_call


@pytest.mark.asyncio
async def test_explicit_intent_executes_without_confirmation_and_renders_real_fields(harness):
    """An explicit change executes once and its card resolves the actual saved value locally."""
    writes = add_future_interface(harness)
    fixed_decisions(harness, [widget_call(), {'decision': 'answer', 'answer': '改好了。', 'cards': [
        {'kind': 'result', 'title': '本次操作已完成', 'blocks': [{'type': 'fields', 'items': [
            {'label': '名称', 'value': {'$result': '/title'}}]}]}]}])
    body = {'text': '把测试物件widget-001名称改成新名称，原版本2', 'idempotency_key': 'direct-change-001'}
    result = await harness.client.post('/api/v1/matters/intake', headers=auth(harness.token_a), json=body)
    assert result.status_code == 200, result.text
    assert result.json()['outcome'] == 'answer'
    card = result.json()['pending_context']['cards'][0]
    assert card['status'] == 'completed'
    assert card['blocks'][0]['items'][0]['value'] == '新名称'
    assert writes == [('widget-001', '新名称')]
    replay = await harness.client.post('/api/v1/matters/intake', headers=auth(harness.token_a), json=body)
    assert replay.status_code == 200 and writes == [('widget-001', '新名称')]
    assert '/api/v1/matters/intake/{local_run_id}/confirm-operation' not in harness.app.openapi()['paths']


@pytest.mark.asyncio
async def test_unclear_target_asks_user_without_mutating(harness):
    """AI asks for missing information instead of presenting a redundant confirmation."""
    writes = add_future_interface(harness)
    fixed_decisions(harness, [{'decision': 'ask_user', 'question': '你想改哪一条？', 'choices': ['上午那条', '下午那条']}])
    result = await harness.client.post('/api/v1/matters/intake', headers=auth(harness.token_a), json={'text': '把那条改一下', 'idempotency_key': 'ask-target-001'})
    assert result.status_code == 200 and result.json()['outcome'] == 'needs_clarification'
    assert result.json()['pending_context']['choices'] == ['上午那条', '下午那条']
    assert writes == []


def test_plan_card_supports_multiple_places_and_images_without_claiming_saved():
    """Travel nodes and images use the same card protocol without a business-specific registry."""
    cards = response_cards.prepare_cards([{'kind': 'plan', 'title': '周末慢慢逛', 'blocks': [
        {'type': 'timeline', 'items': [
            {'title': '第一站', 'time': '13:00–14:00', 'image': {'src': 'https://images.example.com/place.jpg', 'alt': '地点照片'}},
            {'title': '第二站', 'description': '到这里坐一会儿', 'connector': '步行约十分钟'}]},
        {'type': 'text', 'text': '时间可以按你的节奏调整。'}]}], None)
    assert cards[0]['status'] == 'suggestion'
    assert len(cards[0]['blocks'][0]['items']) == 2
    mapped = response_cards.prepare_cards([{'kind': 'result', 'title': '查询结果', 'blocks': [
        {'type': 'timeline', 'items': {'$each': '/places', 'template': {
            'title': {'$item': '/name'}, 'image': {'src': {'$item': '/image_url'}, 'alt': '地点图片'},
            'fields': [{'label': '地址', 'value': {'$item': '/address'}}]}}}]}], {
                'status': 'succeeded', 'data': {'places': [
                    {'name': '展厅', 'image_url': '/hall.png', 'address': '一号路'},
                    {'name': '公园', 'image_url': '/park.png', 'address': '二号路'}]}})
    assert [item['title'] for item in mapped[0]['blocks'][0]['items']] == ['展厅', '公园']
    assert mapped[0]['blocks'][0]['items'][1]['fields'][0]['value'] == '二号路'
    assert mapped[0]['status'] == 'completed'
    with pytest.raises(ValueError):
        response_cards.prepare_cards([{'kind': 'result', 'title': '已保存', 'blocks': []}], None)
    with pytest.raises(ValueError):
        response_cards.prepare_cards([{'kind': 'info', 'title': '图片', 'blocks': [{'type': 'image', 'src': 'javascript:alert(1)', 'alt': '图'}]}], None)


@pytest.mark.asyncio
async def test_parallel_submissions_dispatch_the_change_once(harness):
    """Concurrent copies of one explicit input cannot perform the business write twice."""
    writes = add_future_interface(harness)
    fixed_decisions(harness, [widget_call(), {'decision': 'answer', 'answer': '改好了。'}])
    body = {'text': '修改测试物件名称', 'idempotency_key': 'direct-parallel-001'}
    results = await asyncio.gather(*[harness.client.post('/api/v1/matters/intake', headers=auth(harness.token_a), json=body) for _ in range(2)])
    assert all(r.status_code in (200, 202) for r in results), [(r.status_code, r.text[:600]) for r in results]
    assert writes == [('widget-001', '新名称')]
