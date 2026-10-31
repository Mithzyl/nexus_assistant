"""Offline App boundary tests; upstream persistence is verified separately."""
import json
from uuid import uuid4

import httpx
import pytest
from conftest import auth


@pytest.mark.asyncio
async def test_management_without_run_preserves_identity_key_and_versions(harness):
    """New accounts use App management and cannot choose an owner or global instructions."""
    h = harness
    calls = []

    async def upstream(request):
        """Record the trusted identity and return a bounded synthetic response."""
        calls.append(request)
        assert request.headers['x-external-user-id'] == h.user_a
        assert request.headers['x-application-id'] == 'personal_assistant'
        if request.method == 'GET':
            return httpx.Response(200, json={'items': [], 'next_offset': None})
        return httpx.Response(200, json={'status': 'active', 'version': 4, 'applied_version': 3})

    nexus = h.app.state.nexus_client
    old = nexus._client
    nexus._client = httpx.AsyncClient(base_url='http://nexus.test', transport=httpx.MockTransport(upstream))
    try:
        response = await h.client.get('/api/v1/memory', headers=auth(h.token_a))
        assert response.status_code == 200
        assert calls[-1].url.path == '/api/v1/app/me/user-memories'
        payload = {'action': 'save', 'text': '先给结论', 'key': 'stable-operation'}
        for _ in range(2):
            response = await h.client.post('/api/v1/memory/commands', headers=auth(h.token_a), json=payload)
            assert response.status_code == 200
            assert response.json()['applied_version'] == 3
        assert calls[-1].content == calls[-2].content
        assert json.loads(calls[-1].content)['key'] == 'stable-operation'
        for extra in ({'user_id': h.user_b}, {'scope': 'project'}, {'action': 'automatic_sharing'}):
            response = await h.client.post('/api/v1/memory/commands', headers=auth(h.token_a), json={**payload, **extra})
            assert response.status_code == 422
        response = await h.client.post('/api/v1/memory/instructions', headers=auth(h.token_a), json={
            'scope': 'global', 'text': 'test', 'enabled': True, 'expected_version': 0})
        assert response.status_code == 422
        response = await h.client.post('/api/v1/memory/instructions', headers=auth(h.token_a), json={
            'text': '先给结论', 'enabled': True, 'expected_version': 0})
        assert response.status_code == 200
        assert json.loads(calls[-1].content)['allowed_entries'] == []
    finally:
        await nexus._client.aclose()
        nexus._client = old


@pytest.mark.asyncio
async def test_invalid_typed_memory_and_undo_are_rejected_before_upstream(harness):
    """Reject malformed coordinates, dates and missing version before any external call."""
    h = harness
    for typed in [
        {'memory_type': 'place', 'label': '家', 'address': '测试路', 'coordinate_system': 'GCJ02', 'latitude': 31},
        {'memory_type': 'time_preference', 'activity': '读书', 'preferred_time': '25:00', 'timezone': 'Asia/Shanghai'},
        {'memory_type': 'experience', 'occurred_on': '2999-01-01', 'description': '未来'},
    ]:
        response = await h.client.post('/api/v1/memory/commands', headers=auth(h.token_a), json={
            'action': 'save', 'text': 'test', 'key': 'invalid', 'typed_data': typed})
        assert response.status_code == 422
    response = await h.client.post('/api/v1/memory/commands', headers=auth(h.token_a), json={
        'action': 'undo', 'entry_id': str(uuid4()), 'key': 'undo'})
    assert response.status_code == 422


def test_intake_retains_literal_user_quote():
    """Source quotes containing newlines or quotes must occur literally in the saved Run."""
    from assistant_api.services.matter_plan import prepare_input
    text = '我家在“测试路”\n门牌是 "12" 号'
    assert text in prepare_input({'text': text, 'now': '2026-10-31T10:00:00+08:00', 'timezone': 'Asia/Shanghai'})


@pytest.mark.asyncio
async def test_memory_conflict_and_failure_are_not_success_or_empty(harness):
    """Preserve a conflict status for fresh confirmation and a failure for unavailable lists."""
    h = harness
    async def upstream(request):
        """Return controlled Nexus statuses without any storage or provider request."""
        return httpx.Response(409 if request.method == 'POST' else 503, json={'detail': {'code': 'memory_version_conflict'}})
    nexus=h.app.state.nexus_client
    old=nexus._client
    nexus._client=httpx.AsyncClient(base_url='http://nexus.test',transport=httpx.MockTransport(upstream))
    try:
        response=await h.client.post('/api/v1/memory/commands',headers=auth(h.token_a),json={'action':'settings','key':'conflict','expected_version':0,'enabled':False})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'memory_version_conflict'
        assert 'HTTP' not in response.json()['error']['message']
        response=await h.client.get('/api/v1/memory',headers=auth(h.token_a))
        assert response.status_code == 503 and 'items' not in response.json()
    finally:
        await nexus._client.aclose();nexus._client=old


@pytest.mark.asyncio
async def test_shared_consent_precedes_workflow_and_cannot_change_on_retry(harness):
    """Shared grant consent uses its version and is pinned before any model execution."""
    h=harness; grant=str(uuid4()); calls=[]
    async def upstream(request):
        """Verify only the session user and saved Run can authorize a shared grant."""
        calls.append(request.url.path)
        if request.url.path.endswith('/shared-memory-disclosures'):
            body=json.loads(request.content)
            assert body['grant_id']==grant and body['expected_version']==7 and body['confirmed'] is True
            assert request.headers['x-external-user-id']==h.user_a
            return httpx.Response(200,json={'grant_id':grant,'version':7})
        if request.url.path.endswith('/agent-workflows'):
            assert any(p.endswith('/shared-memory-disclosures') for p in calls)
        return await h.fake.handler(request)
    nexus=h.app.state.nexus_client;old=nexus._client
    nexus._client=httpx.AsyncClient(base_url='http://nexus.test',transport=httpx.MockTransport(upstream))
    payload={'text':'使用共享给我的测试信息','idempotency_key':'shared-consent-test','memory_disclosures':[{'grant_id':grant,'expected_version':7,'confirmed':True}]}
    try:
        r=await h.client.post('/api/v1/asks',headers=auth(h.token_a),json=payload)
        assert r.status_code==202
        r=await h.client.post('/api/v1/asks',headers=auth(h.token_a),json={**payload,'memory_disclosures':[]})
        assert r.status_code==409
    finally:
        await nexus._client.aclose();nexus._client=old
