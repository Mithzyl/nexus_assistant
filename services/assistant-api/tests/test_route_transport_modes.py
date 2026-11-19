"""Verify newly supported AMap modes and the exact capabilities offered to an Agent.

Only local HTTP fixtures and isolated application metadata are used here; the
separate authorized real Agent task checks actual provider and browser behavior.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from assistant_api.commute.providers.amap import AmapProvider
from assistant_api.services.commute_estimate import estimate
from assistant_api.schemas.matters import EstimateResponse


@pytest.mark.parametrize('mode', ['walking', 'bicycling', 'electrobike'])
async def test_amap_active_modes_return_duration_and_own_geometry(mode):
    """Use v5 cost.duration and preserve the requested mode in both API and geometry."""
    now=datetime(2026,11,6,tzinfo=UTC)

    def reply(request):
        """Model the documented v5 fields, with no v3 top-level duration fallback."""
        assert request.url.path == '/v5/direction/'+mode
        assert request.url.params['show_fields'] == 'cost,polyline'
        assert request.url.params['origin'] == '121.470000,31.230000'
        return httpx.Response(200,json={'status':'1','route':{'paths':[{
            'distance':'800','cost':{'duration':'600'},
            'steps':[{'polyline':'121.470000,31.230000;121.475000,31.235000'}]}]}})

    provider=AmapProvider(api_key='fixture-key',now=lambda:now,transport=httpx.MockTransport(reply))
    try:
        result=await estimate(SimpleNamespace(eta=provider,now=lambda:now),
            origin=(31.23,121.47),destination=(31.235,121.475),mode=mode,
            depart_at=now,arrive_by=None,origin_coordinate_system='gcj02',destination_coordinate_system='gcj02')
        response=EstimateResponse.model_validate(result)
        assert response.status == 'available'
        assert response.mode == mode and response.duration_seconds == 600
        assert response.shape.segments[0].kind == mode
        assert len(response.shape.segments[0].points) == 2
        assert response.fare_cny is None
    finally:
        await provider.aclose()


async def test_agent_context_publishes_current_route_capabilities_and_budget(harness):
    """Expose configured modes and a finite frozen budget without exposing provider credentials."""
    from assistant_api.commute.runtime import route_capabilities
    from assistant_api.services.operation_intake import model_context, workflow_configuration
    from assistant_api.services.api_operations import OperationCatalog

    capabilities=route_capabilities(SimpleNamespace(eta=SimpleNamespace(
        provider='amap',available=True,supported_modes=('driving','walking','transit','bicycling','electrobike'))))
    flow={'text':'查询通勤路线','query':'查询通勤路线','now':'2026-11-06T00:00:00Z',
          'timezone':'Asia/Shanghai','history':[],'receipts':[],'step':0,
          'route_capabilities':capabilities,'agent_budget':{'max_model_calls':24,'wall_time_limit_ms':600000}}
    context=model_context(flow,OperationCatalog(harness.app))
    assert context['route_capabilities']['supported_modes'] == ['driving','walking','transit','bicycling','electrobike']
    configuration=workflow_configuration(context)
    assert configuration['max_model_calls'] == 24
    assert configuration['wall_time_limit_ms'] == 600000
    assert '不得擅自更换' in configuration['application_protocol_instructions']
    unavailable=route_capabilities(SimpleNamespace(eta=SimpleNamespace(provider='unavailable',available=False)))
    assert unavailable['supported_modes'] == []


async def test_commute_quote_measures_new_mode_and_reuses_existing_modes(harness):
    """Changing only mode measures its missing route and retains completed comparison results."""
    from datetime import timedelta
    from assistant_api.models import MatterDraft
    from assistant_api.services.commute_places import sign_selection
    from conftest import auth
    from assistant_api.commute.providers.mock import MockEtaProvider

    provider=MockEtaProvider(now=lambda:datetime.now(UTC))
    harness.app.state.commute_runtime.eta=provider
    day=(datetime.now(UTC)+timedelta(days=2)).date()
    async with harness.app.state.session_factory() as db:
        draft=MatterDraft(user_id=harness.user_a,kind='create',status='preview',version=1,
            payload_json={'kind':'create','title':'方式验证','goal':'测试','source_text':'测试',
                'schedule':None,'todos':[],'complete_todo_ids':[],'matter_action':'none',
                'commute':{'status':'needs_selection','timezone':'Asia/Shanghai',
                    'intent':{'arrival_local_time':'16:30'}}})
        db.add(draft)
        await db.commit()
        draft_id=draft.draft_id
    token=sign_selection({'name':'样本地点','city':'上海','latitude':31.23,'longitude':121.47,
        'coordinate_system':'gcj02'},harness.settings.tool_callback_token.get_secret_value(),
        user_id=harness.user_a,draft_id=draft_id,selection_kind='place')
    body={'expected_version':1,'origin_token':token,'destination_token':token,
          'effective_from':str(day),'weekdays':[day.isoweekday()],'buffer_minutes':10,'mode':'walking'}
    first=await harness.client.post(f'/api/v1/matters/drafts/{draft_id}/commute-quote',
        headers=auth(harness.token_a),json=body)
    assert first.status_code == 200, first.text
    original=first.json()['payload']['commute']['routes']
    second=await harness.client.post(f'/api/v1/matters/drafts/{draft_id}/commute-quote',
        headers=auth(harness.token_a),json={**body,'expected_version':first.json()['version'],'mode':'electrobike'})
    assert second.status_code == 200, second.text
    routes=second.json()['payload']['commute']['routes']
    assert routes['walking'] == original['walking']
    assert routes['electrobike']['status'] == 'available'
    assert second.json()['payload']['commute']['selected_mode'] == 'electrobike'
    assert [call['mode'] for call in provider.calls] == ['driving','transit','walking','electrobike']
