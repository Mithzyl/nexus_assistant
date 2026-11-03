"""Focused clarification path discovered while evaluating same-title calendar records."""
import pytest
from conftest import auth
from test_api_operation_flow import fixed_decisions


@pytest.mark.asyncio
async def test_ambiguous_result_is_returned_to_ai_for_specific_question(harness):
    """A rejected local selector performs no update and gives AI a chance to ask for the date."""
    h = harness
    first = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'ambiguous-first-001', 'title': '项目评审', 'starts_at': '2030-07-01T15:00:00+08:00'})
    assert first.status_code == 201, first.text
    mid = first.json()['matter']['matter_id']
    second = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'ambiguous-second-001', 'matter_id': mid, 'expected_matter_version': 1,
        'title': '项目评审', 'starts_at': '2030-07-02T15:00:00+08:00'})
    assert second.status_code == 201, second.text
    fixed_decisions(h, [
        {'decision': 'call', 'call': {'operation_id': 'get_matter_api_v1_matters__matter_id__get', 'path_parameters': {'matter_id': mid}, 'summary': '读取当前事项日程'}},
        {'decision': 'call', 'call': {'operation_id': 'update_schedule', 'path_parameters': {'schedule_id': {'$result': '/schedules', 'where': {'title': '项目评审'}, 'field': 'schedule_id'}}, 'body': {'idempotency_key': 'ambiguous-update-001', 'expected_matter_version': 2, 'starts_at': '2030-07-01T17:00:00+08:00'}, 'summary': '修改原日程'}},
        {'decision': 'ask_user', 'question': '你要改哪一天的项目评审？请告诉我日期。'},
    ])
    result = await h.client.post(f'/api/v1/matters/{mid}/intake', headers=auth(h.token_a), json={
        'text': '查询当前事项的项目评审日程，改到下午五点', 'idempotency_key': 'ambiguous-question-flow-001'})
    assert result.status_code == 200, result.text
    assert result.json()['clarify'] == '你要改哪一天的项目评审？请告诉我日期。'
    detail = (await h.client.get(f'/api/v1/matters/{mid}', headers=auth(h.token_a))).json()
    assert [row['starts_at'] for row in detail['schedules']] == ['2030-07-01T07:00:00Z', '2030-07-02T07:00:00Z']
    prompts = '\n'.join(call['body'] for call in h.fake.calls if call['path'] == '/api/v1/app/runs')
    assert 'argument_feedback' in prompts and 'api_result_selection_invalid' in prompts
