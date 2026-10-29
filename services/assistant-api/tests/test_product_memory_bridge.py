"""Focused offline contract checks; these are not real-model acceptance evidence."""

import json
from uuid import uuid4

import httpx
import pytest

from assistant_api.models import NexusRun
from conftest import auth


@pytest.mark.asyncio
async def test_product_memory_owner_boundary_and_data(harness):
    h = harness
    local, remote = str(uuid4()), str(uuid4())
    async with h.app.state.session_factory() as db:
        db.add(NexusRun(local_run_id=local, nexus_run_id=remote, user_id=h.user_a,
                        idempotency_key="product-memory-fixture", request_text="fixture"))
        await db.commit()
    calls = []

    async def upstream(request):
        calls.append(request)
        assert request.headers["x-external-user-id"] == h.user_a
        assert request.url.path == f"/api/v1/app/runs/{remote}/user-memories"
        if request.method == "POST":
            body = json.loads(request.content)
            assert "local_run_id" not in body
            assert body["scope"] == "personal"
            assert body["app_data"]["fields"] == {"arrival_local_time": "08:00"}
            assert body["confirmed_sensitive"] is False
            return httpx.Response(200, json={"status": "candidate", "entry_id": "entry", "version": 1})
        return httpx.Response(200, json={"items": [{"entry_id": "entry", "text": "通勤资料", "status": "candidate", "version": 2, "sensitive": True, "app_data": {"namespace": "personal_assistant", "schema_version": 1, "fields": {"arrival_local_time": "08:00"}}}], "next_offset": 10})

    h.fake.handler = upstream
    # MockTransport retains the original bound handler; replace only this test client's transport.
    nexus = h.app.state.nexus_client
    old = nexus._client
    nexus._client = httpx.AsyncClient(base_url="http://nexus.test", transport=httpx.MockTransport(upstream))
    try:
        r = await h.client.get("/api/v1/memory", headers=auth(h.token_b))
        assert r.status_code == 409
        r = await h.client.get("/api/v1/memory", params={"local_run_id": local}, headers=auth(h.token_b))
        assert r.status_code == 404
        assert not calls
        r = await h.client.post("/api/v1/memory/search", headers=auth(h.token_a), json={"query_text": "通勤", "idempotency_key": "memory-search-fixture"})
        assert r.status_code == 200
        hit = r.json()["items"][0]
        assert hit["status"] == "candidate" and hit["version"] == 2
        assert hit["app_data"]["fields"]["arrival_local_time"] == "08:00"
        assert r.json()["next_cursor"] == "10"
        payload = {"local_run_id": local, "action": "propose", "key": "memory-fixture", "text": "通勤资料", "app_data": {"fields": {"arrival_local_time": "08:00"}}}
        r = await h.client.post("/api/v1/memory/commands", headers=auth(h.token_a), json=payload)
        assert r.status_code == 200 and r.json()["status"] == "candidate"
        r = await h.client.post("/api/v1/memory/commands", headers=auth(h.token_a), json={**payload, "user_id": h.user_b})
        assert r.status_code == 422
        r = await h.client.post("/api/v1/memory/commands", headers=auth(h.token_b), json=payload)
        assert r.status_code == 404
        assert len(calls) == 2
    finally:
        await nexus._client.aclose()
        nexus._client = old


@pytest.mark.asyncio
async def test_explicit_disclosure_precedes_model_and_replay_cannot_change_it(harness):
    h = harness
    entry = str(uuid4())
    calls = []
    denied = True

    async def upstream(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/user-memories'):
            body = json.loads(request.content)
            assert body['action'] == 'disclose' and body['confirmed_sensitive'] is True
            assert body['scope'] == 'personal' and body['entry_id'] == entry
            assert request.headers['x-external-user-id'] == h.user_a
            return httpx.Response(409 if denied else 200, json={'status': 'active', 'version': 2})
        if request.url.path.endswith('/agent-workflows'):
            assert any(p.endswith('/user-memories') for p in calls)
        return await h.fake.handler(request)

    nexus = h.app.state.nexus_client
    old = nexus._client
    nexus._client = httpx.AsyncClient(base_url='http://nexus.test', transport=httpx.MockTransport(upstream))
    payload = {'text': 'Read the selected test memory', 'idempotency_key': 'explicit-disclosure-fixture',
               'memory_disclosures': [{'entry_id': entry, 'expected_version': 1, 'confirmed_sensitive': True}]}
    try:
        r = await h.client.post('/api/v1/asks', headers=auth(h.token_a), json=payload)
        assert r.status_code >= 400
        assert not any(p.endswith('/agent-workflows') for p in calls)
        denied = False
        r = await h.client.post('/api/v1/asks', headers=auth(h.token_a), json=payload)
        assert r.status_code == 202
        assert sum(p == '/api/v1/app/runs' for p in calls) == 1
        assert any(p.endswith('/agent-workflows') for p in calls)
        before = [p for p in calls if p.endswith('/user-memories') or p.endswith('/agent-workflows')]
        r = await h.client.post('/api/v1/asks', headers=auth(h.token_a), json={**payload, 'memory_disclosures': []})
        assert r.status_code == 409
        assert [p for p in calls if p.endswith('/user-memories') or p.endswith('/agent-workflows')] == before
    finally:
        await nexus._client.aclose()
        nexus._client = old
