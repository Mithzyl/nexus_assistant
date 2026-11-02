"""Focused plan location search boundary; no model or real map service requests."""
import pytest
from sqlalchemy import func, select
from assistant_api.models import NexusRun, MatterDraft

@pytest.mark.asyncio
async def test_search_needs_session_but_no_draft_or_run(commute_harness):
    from assistant_api.commute.providers.mock import MockPlaceProvider
    h = commute_harness
    h.runtime.places = MockPlaceProvider()
    r = await h.client.get('/api/v1/commute/places?query=测试地点')
    assert r.status_code == 401
    r = await h.client.get('/api/v1/commute/places', params={'query': '测试地点'},
                           headers={'Authorization': 'Bearer '+h.token_a})
    assert r.status_code == 200
    assert r.json()['candidates']
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(NexusRun)) == 0
        assert await db.scalar(select(func.count()).select_from(MatterDraft)) == 0
