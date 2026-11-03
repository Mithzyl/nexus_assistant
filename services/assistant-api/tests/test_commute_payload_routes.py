"""Focused checks for moving commute service_date into a JSON request body."""
from datetime import UTC, datetime
import pytest
from conftest import auth
from test_three_operation_repairs import seed_commute
from assistant_api.models import FollowUp
from assistant_api.services import commute_lifecycle, follow_ups
from sqlalchemy import select


def test_published_day_operations_accept_date_in_payload(harness):
    """AI sees a typed date in each request body and only plan_id in path parameters."""
    spec=harness.app.openapi()
    operations={op['operationId']:(method,path,op) for path,item in spec['paths'].items() for method,op in item.items() if method in ('get','post','patch')}
    expected={'get_commute_day':('post','/api/v1/commute-plans/{plan_id}/days/query'),
              'update_commute_day':('patch','/api/v1/commute-plans/{plan_id}/days'),
              'refresh_commute_day':('post','/api/v1/commute-plans/{plan_id}/days/refresh')}
    for operation_id,(method,path) in expected.items():
        actual_method,actual_path,op=operations[operation_id]
        assert (actual_method,actual_path)==(method,path)
        assert [p['name'] for p in op['parameters'] if p['in']=='path']==['plan_id']
        ref=op['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
        schema=spec['components']['schemas'][ref]
        assert 'service_date' in schema['required'] and schema['properties']['service_date']['format']=='date'


@pytest.mark.asyncio
async def test_payload_query_update_and_refresh_reuse_same_day(harness):
    """All three public operations use body dates while preserving the original saved schedule."""
    h=harness;seeded=await seed_commute(h);base=f"/api/v1/commute-plans/{seeded['plan_id']}/days"
    response=await h.client.post(base+'/query',headers=auth(h.token_a),json={'service_date':'2030-11-04'})
    assert response.status_code==200,response.text
    day=response.json()
    updated=await h.client.patch(base,headers=auth(h.token_a),json={
        'service_date':'2030-11-04','arrival_local_time':'10:00','expected_plan_revision':day['revision'],
        'expected_matter_version':day['version'],'idempotency_key':'payload-date-update'})
    assert updated.status_code==200,updated.text
    assert updated.json()['schedule']['schedule_id']==seeded['schedule']['schedule_id']
    assert updated.json()['schedule']['starts_at']=='2030-11-04T01:20:00Z'
    refreshed=await h.client.post(base+'/refresh',headers=auth(h.token_a),json={
        'service_date':'2030-11-04','idempotency_key':'payload-date-refresh'})
    assert refreshed.status_code==200,refreshed.text
    assert refreshed.json()['schedule']['schedule_id']==seeded['schedule']['schedule_id']
    assert refreshed.json()['target_arrival_at']=='2030-11-04T02:00:00Z'
    missing=await h.client.post(base+'/query',headers=auth(h.token_a),json={})
    assert missing.status_code==422


@pytest.mark.asyncio
async def test_evening_rule_persists_post_read_payload(harness):
    """Durable follow-ups execute the published read-only POST with the saved service date."""
    h=harness;await seed_commute(h);h.app.state.settings.commute_auto_ai_enabled=True
    now=datetime(2030,11,3,14,tzinfo=UTC)
    await commute_lifecycle.materialize_follow_up_rules(h.app,now=now)
    async with h.app.state.session_factory() as db:
        rules=list(await db.scalars(select(FollowUp).where(FollowUp.user_id==h.user_a)))
        evening=next(row for row in rules if ':evening:' in row.idempotency_key)
        ref=evening.references_json[0]
        assert ref['body']=={'service_date':'2030-11-04'}
        assert 'service_date' not in ref['path_parameters']
        result=await follow_ups.read_references(h.app,h.user_a,[ref])
        assert result['commute_day']['service_date']=='2030-11-04'
