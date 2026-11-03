"""Focused acceptance for runtime OpenAPI discovery, privacy and locally resolved calls.

Uses the real ASGI app, session authentication and temporary SQLite. Nexus output
is fixed here; the separate live check validates model generation. No provider,
map or notification calls are made by this module.
"""
import json
import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from assistant_api.core.deps import CurrentUser
from assistant_api.services import api_operations
from assistant_api.models import Matter, MatterDraft, ScheduleEvent
from conftest import auth


class WidgetChange(BaseModel):
    """Describe a future business interface unknown to the tool executor."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int
    title: str


def add_future_interface(harness):
    """Add only a business route and parameter schema, never a tool registration."""
    writes = []

    @harness.app.patch(
        "/api/v1/future-widgets/{widget_id}", operation_id="change_future_widget",
        summary="修改测试物件名称", description="修改已有物件；需要原ID与版本，找不到不可新增。",
    )
    async def change_widget(widget_id: str, payload: WidgetChange, user: CurrentUser):
        """Apply one synthetic owned-object update and return its saved fields."""
        if user.user_id != harness.user_a:
            raise HTTPException(403, "无权修改该物件")
        if payload.expected_version != 2:
            raise HTTPException(409, "版本已改变")
        writes.append((widget_id, payload.title))
        return {"object_id": widget_id, "title": payload.title, "version": 3}

    harness.app.openapi_schema = None
    return writes


def fixed_decisions(harness, values):
    """Keep the existing fake Nexus transport but supply one output per model step."""
    pending = iter(values)
    original = harness.fake.handler
    outputs = {}

    async def handle(request):
        """Supply public JSON on result reads, preserving real client/workflow calls."""
        if request.url.path.endswith("/result"):
            workflow = request.url.path.split('/')[-2]
            if workflow not in outputs:
                value = next(pending)
                if callable(value):
                    value = await value()
                outputs[workflow] = json.dumps(value, ensure_ascii=False)
            harness.fake.final_text = outputs[workflow]
        return await original(request)

    harness.app.state.nexus_client._client._transport = httpx.MockTransport(handle)


def widget_call(**body):
    """Describe the future interface using only its OpenAPI operation and parameters."""
    return {"decision": "call", "call": {"operation_id": "change_future_widget", "path_parameters": {"widget_id": "widget-001"}, "query_parameters": {}, "body": {"expected_version": 2, "title": "新名称", **body}, "summary": "把物件名称改为新名称"}}


@pytest.mark.asyncio
async def test_invalid_fields_and_foreign_permissions_never_execute(harness):
    """Unknown fields and original route permissions reject invalid automatic writes."""
    h = harness
    writes = add_future_interface(h)
    fixed_decisions(h, [widget_call(admin=True)])
    bad = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_a), json={'text': '修改测试物件名称', 'idempotency_key': 'invalid-fields-001'})
    assert bad.status_code in (400, 422), bad.text
    assert writes == []
    fixed_decisions(h, [widget_call(), {'decision': 'answer', 'answer': '这次没有修改权限。'}])
    denied = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_b), json={'text': '修改测试物件名称', 'idempotency_key': 'foreign-call-001'})
    assert denied.status_code == 200
    assert denied.json()['pending_context']['execution_result']['http_status'] == 403
    assert writes == []


@pytest.mark.asyncio
async def test_existing_route_permissions_and_conflicts_are_preserved(harness):
    """The generic caller uses the current session and surfaces actual HTTP failure receipts."""
    h = harness
    writes = add_future_interface(h)
    fixed_decisions(h, [widget_call(expected_version=1), {'decision': 'answer', 'answer': '版本已改变，未能更新，请核对。'}])
    result = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_a), json={'text': '修改测试物件名称', 'idempotency_key': 'conflict-call-001'})
    assert result.status_code == 200, result.text
    receipt = result.json()['pending_context']['execution_result']
    assert receipt['status'] == 'failed' and receipt['http_status'] == 409
    assert writes == []


@pytest.mark.asyncio
async def test_catalog_rejects_unknown_operations_and_keeps_required_headers(harness):
    """Only published operations can run; OpenAPI references and header requirements survive retrieval."""
    catalog = api_operations.OperationCatalog(harness.app)
    selected = catalog.search('确认日程草稿', limit=6)
    confirm = catalog.operations['confirm_draft_api_v1_matters_drafts__draft_id__confirm_post']
    assert any(p['name'] == 'Idempotency-Key' for p in confirm.definition['parameters'])
    assert selected
    assert all('/auth/' not in op.path and '/intake' not in op.path for op in catalog.operations.values())
    with pytest.raises(ValueError):
        catalog.validate_call({'operation_id': 'invented_operation', 'path_parameters': {}, 'query_parameters': {}, 'body': {}})


@pytest.mark.asyncio
async def test_existing_schedule_uses_real_draft_patch_confirm_without_duplicate(harness):
    """An old schedule is selected by local result references, retaining its ID through real endpoints."""
    h = harness
    async with h.app.state.session_factory() as db:
        matter = Matter(user_id=h.user_a, title='原会议', status='active')
        db.add(matter)
        await db.flush()
        schedule = ScheduleEvent(user_id=h.user_a, matter_id=matter.matter_id, title='原会议', starts_at=datetime(2026, 10, 30, 1, tzinfo=UTC), location='旧地点', status='scheduled', source='web_native')
        db.add(schedule)
        await db.commit()
        mid, sid = matter.matter_id, schedule.schedule_id

    fixed_decisions(h, [
        {'decision': 'call', 'call': {'operation_id': 'get_matter_api_v1_matters__matter_id__get', 'path_parameters': {'matter_id': mid}, 'summary': '先查询原事项日程详情'}},
        {'decision': 'call', 'call': {'operation_id': 'create_edit_draft_api_v1_matters__matter_id__drafts_post', 'path_parameters': {'matter_id': mid}, 'body': {'idempotency_key': 'existing-draft-key', 'schedule_id': {'$result': '/schedules', 'where': {'starts_at': '2026-10-30T09:00:00+08:00', 'location': '旧地点'}, 'field': 'schedule_id'}}, 'summary': '准备原会议修改草稿'}},
        {'decision': 'call', 'call': {'operation_id': 'patch_draft_api_v1_matters_drafts__draft_id__patch', 'path_parameters': {'draft_id': {'$result': '/draft_id'}}, 'body': {'expected_version': {'$result': '/version'}, 'schedule': {'$result': '/payload/schedule', 'overrides': {'starts_at': '2030-01-03T15:00:00+08:00', 'location': '新地点'}}}, 'summary': '修改原会议草稿并保留其他字段'}},
        {'decision': 'search', 'search_query': 'confirm draft 确认保存草稿'},
        {'decision': 'call', 'call': {'operation_id': 'confirm_draft_api_v1_matters_drafts__draft_id__confirm_post', 'path_parameters': {'draft_id': {'$result': '/draft_id'}}, 'body': {'expected_version': {'$result': '/version'}}, 'summary': '保存原会议的新时间和地点'}},
        {'decision': 'answer', 'answer': '原会议已更新为2030年1月3日15点，新地点。'},
    ])
    pending = await h.client.post(f'/api/v1/matters/{mid}/intake', headers=auth(h.token_a), json={'text': '查询事项日程详情，修改原会议，2026年10月30日9点旧地点那条，改为2030年1月3日15点新地点，先准备编辑草稿再确认', 'idempotency_key': 'original-schedule-001'})
    assert pending.status_code == 200, pending.text
    assert pending.json()['outcome'] == 'answer'
    async with h.app.state.session_factory() as db:
        schedules = list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.matter_id == mid)))
        assert len(schedules) == 1
        assert schedules[0].schedule_id == sid and schedules[0].location == '新地点'
    prompts = '\n'.join(c['body'] for c in h.fake.calls if c['path'] == '/api/v1/app/runs')
    assert '血压正常' not in prompts and '口腔医院' not in prompts and '交物业费' not in prompts


@pytest.mark.asyncio
async def test_read_response_body_stays_local_and_history_is_account_scoped(harness):
    """Query result values stay out of Nexus follow-up inputs, while the user gets actual data."""
    h = harness

    @h.app.get('/api/v1/future-private-result', operation_id='read_future_result', summary='查询本次结果')
    async def read_result(user: CurrentUser):
        """Return a synthetic private value through an authenticated business interface."""
        return {'title': 'private-value-not-for-nexus-718', 'object_id': 'result-718'}

    h.app.openapi_schema = None
    fixed_decisions(h, [
        {'decision': 'call', 'call': {'operation_id': 'read_future_result', 'summary': '查询本次结果'}},
        {'decision': 'answer', 'answer': '已取得结果，请查看下方卡片。'},
    ])
    response = await h.client.post('/api/v1/matters/intake', headers=auth(h.token_a), json={'text': '查询本次结果', 'idempotency_key': 'privacy-read-001'})
    assert response.status_code == 200, response.text
    assert response.json()['pending_context']['execution_result']['data']['title'] == 'private-value-not-for-nexus-718'
    prompts = '\n'.join(c['body'] for c in h.fake.calls if c['path'] == '/api/v1/app/runs')
    assert 'private-value-not-for-nexus-718' not in prompts
    assert 'result-718' in prompts



def test_local_result_reference_selects_original_id_without_disclosing_record_body():
    """Resolve the original record locally with equivalent timezone instants and unchanged fields."""
    receipt = {'status': 'succeeded', 'http_status': 200, 'operation_id': 'read_any', 'data': {
        'schedules': [{'schedule_id': 'original-id', 'starts_at': '2026-10-30T01:00:00Z', 'location': '原地点'},
                      {'schedule_id': 'other-id', 'starts_at': '2026-10-30T07:00:00Z', 'location': '另一地点'}],
        'payload': {'schedule': {'title': '旧标题', 'starts_at': '2026-10-30T01:00:00Z', 'location': '原地点'}}}}
    call = {'body': {'schedule_id': {'$result': '/schedules', 'where': {'starts_at': '2026-10-30T09:00:00+08:00', 'location': '原地点'}, 'field': 'schedule_id'},
                     'schedule': {'$result': '/payload/schedule', 'overrides': {'title': '团建'}}}}
    resolved = api_operations.resolve_result_references(call, receipt)
    assert resolved['body']['schedule_id'] == 'original-id'
    assert resolved['body']['schedule'] == {'title': '团建', 'starts_at': '2026-10-30T01:00:00Z', 'location': '原地点'}
    assert receipt['data']['payload']['schedule']['title'] == '旧标题'
    projected = api_operations.model_receipt(receipt)
    assert projected['result_shape']['/schedules']['count'] == 2
    assert '原地点' not in json.dumps(projected, ensure_ascii=False)
    assert 'original-id' not in json.dumps(projected)
    empty = api_operations.model_receipt({'status': 'succeeded', 'http_status': 200, 'operation_id': 'read', 'data': {'items': []}})
    assert empty['result_shape']['/items']['count'] == 0


def test_local_result_reference_refuses_ambiguous_or_positional_targets():
    """Multiple matches and array positions cannot silently select an object to mutate."""
    receipt = {'status': 'succeeded', 'data': {'items': [{'id': 'a', 'title': '同名'}, {'id': 'b', 'title': '同名'}]}}
    for ref in ({'$result': '/items', 'where': {'title': '同名'}, 'field': 'id'}, {'$result': '/items/0/id'}, {'$result': '/missing'}):
        with pytest.raises(ValueError):
            api_operations.resolve_result_references({'body': {'target_id': ref}}, receipt)
