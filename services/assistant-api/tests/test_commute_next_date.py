from datetime import UTC, datetime
import pytest
from sqlalchemy import func, select
from assistant_api.models import CommuteCheck, CommuteInstance
from tests.commute_flow import create_plan, execute_one_check
from tests.test_commute_api import headers

@pytest.mark.asyncio
@pytest.mark.parametrize(('now', 'overrides', 'expected'), [
 ('2026-10-23T00:00:00', {}, '2026-10-23'),
 ('2026-10-23T09:00:00', {}, '2026-10-26'),
 ('2026-10-23T01:00:00', {}, '2026-10-26'),
 ('2026-10-24T00:00:00', {}, '2026-10-26'),
 ('2026-10-23T00:00:00', {'effective_from': '2026-10-28'}, '2026-10-28'),
 ('2026-10-23T17:00:00', {'weekdays': [6]}, '2026-10-24'),
])
async def test_date_preview_and_replay(commute_harness, now, overrides, expected):
 h = commute_harness
 h.clock.set(datetime.fromisoformat(now).replace(tzinfo=UTC))
 plan = await create_plan(h, h.token_a, **overrides)
 path = f"/api/v1/commute-plans/{plan['id']}"
 preview = await h.client.get(path + '/analysis-target', headers=headers(h.token_a))
 assert preview.status_code == 200, preview.text
 assert preview.json()['local_service_date'] == expected
 async with h.app.state.session_factory() as db:
  assert await db.scalar(select(func.count()).select_from(CommuteCheck)) == 0
  assert await db.scalar(select(func.count()).select_from(CommuteInstance)) == 0
 assert not h.eta.calls and not h.weather.calls
 response = await h.client.post(path + '/checks', headers=headers(h.token_a, {'Idempotency-Key': 'next-date'}))
 assert response.status_code == 202, response.text
 assert response.json()['local_service_date'] == expected
 assert response.json()['target_arrival_at'] == preview.json()['target_arrival_at']
 read = await h.client.get('/api/v1/checks/' + response.json()['check_id'], headers=headers(h.token_a))
 assert read.json()['local_service_date'] == expected
 h.clock.set(datetime(2026, 11, 1, tzinfo=UTC))
 replay = await h.client.post(path + '/checks', headers=headers(h.token_a, {'Idempotency-Key': 'next-date'}))
 assert replay.json()['check_id'] == response.json()['check_id']
 assert replay.json()['local_service_date'] == expected

@pytest.mark.asyncio
async def test_future_execution(commute_harness):
 h = commute_harness
 h.clock.set(datetime(2026, 10, 23, 9, tzinfo=UTC))
 plan = await create_plan(h, h.token_a)
 response = await h.client.post(f"/api/v1/commute-plans/{plan['id']}/checks", headers=headers(h.token_a, {'Idempotency-Key': 'future-worker'}))
 assert response.status_code == 202
 check = await execute_one_check(h)
 assert check.status in {'succeeded', 'degraded'}
 assert h.eta.calls and h.weather.calls
 assert h.weather.calls[0]['window_end'].date().isoformat() == '2026-10-26'

@pytest.mark.asyncio
@pytest.mark.parametrize(('minute', 'expected'), [(29, '2026-10-23'), (30, '2026-10-23'), (31, '2026-10-26')])
async def test_thirty_minute_cutoff(commute_harness, minute, expected):
 h=commute_harness
 h.clock.set(datetime(2026,10,23,0,minute,tzinfo=UTC))
 plan=await create_plan(h,h.token_a)
 response=await h.client.get(f"/api/v1/commute-plans/{plan['id']}/analysis-target",headers=headers(h.token_a))
 assert response.json()['local_service_date']==expected

@pytest.mark.asyncio
async def test_place_metadata_roundtrip(commute_harness):
 from tests.commute_support import commute_plan_body
 h=commute_harness
 body=commute_plan_body()
 for point in [body['origin'],body['destination']]:
  point.update(city='柳州市',coordinate_system='gcj02')
 r=await h.client.post('/api/v1/commute-plans',json=body,headers=headers(h.token_a))
 assert r.status_code==201
 assert r.json()['origin']['city']=='柳州市'
 assert r.json()['destination']['coordinate_system']=='gcj02'
 for point in [body['origin'],body['destination']]:
  del point['city'];del point['coordinate_system']
 r=await h.client.put('/api/v1/commute-plans/'+r.json()['id'],json=body,headers=headers(h.token_a,{'If-Match':'1'}))
 assert r.status_code==200
 assert r.json()['origin']['city']=='柳州市'

@pytest.mark.asyncio
async def test_transit_resolves_missing_cities(commute_harness):
 from types import SimpleNamespace
 from assistant_api.commute.services.checks import _call_eta
 h=commute_harness
 calls=[]
 class Places:
  async def reverse_geocode(self,**kwargs):
   calls.append(kwargs)
   return SimpleNamespace(city='柳州市')
 class Eta:
  provider='amap'
  available=True
  async def fetch(self,**kwargs):
   assert kwargs['origin_city']=='柳州市'
   assert kwargs['destination_city']=='柳州市'
   return 'route-result'
 runtime=SimpleNamespace(eta=Eta(),places=Places(),settings=h.settings)
 plan=SimpleNamespace(transport_mode='transit',origin_city=None,destination_city=None,
  origin_latitude=24.28,origin_longitude=109.43,destination_latitude=24.31,destination_longitude=109.41)
 result,error=await _call_eta(runtime,plan,request_depart_at=h.clock(),target=h.clock())
 assert result=='route-result' and error is None and len(calls)==2
 plan.origin_city=plan.destination_city='柳州市'
 calls.clear()
 result,error=await _call_eta(runtime,plan,request_depart_at=h.clock(),target=h.clock())
 assert result=='route-result' and not calls
