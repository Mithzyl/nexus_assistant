"""Only stream errors, worker continuation and history; no real model."""
import json
from uuid import uuid4
import httpx
import pytest
from assistant_api.nexus_client.client import NexusPilotClient
from assistant_api.nexus_client.models import WorkflowCreateRequest
from assistant_api.models import NexusRun
from conftest import auth
ERROR = dict(error_code='invalid_request', error_type='model_error',public_message='模型供应商拒绝本次请求。',is_retryable=False,outcome_is_known=True,details_reference_id='diagnostic-reference')
def event(seq, state):
    return 'id: '+str(seq)+'\ndata: '+json.dumps(dict(workflow_execution_id='workflow',workflow_status=state))+'\n\n'
@pytest.mark.asyncio
async def test_worker_replay_follows_get_without_second_submission():
    calls=[]
    async def upstream(req):
        calls.append(req.method)
        if req.method=='POST': body=event(1,'pending')
        else:
            assert req.url.params['after_sequence']=='1'
            body=event(2,'failed')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text=body)
    c=NexusPilotClient(base_url='http://nexus.test',application_id='personal_assistant',application_token='x'*32,transport=httpx.MockTransport(upstream))
    try:
        events=[e async for e in c.start_workflow_events('user','run',WorkflowCreateRequest(idempotency_key='stable-key',role_bindings={'controller':{'provider':'opencode','model':'deepseek-v4.1-flash'}}))]
        assert len(events)==2 and calls==['POST','GET']
    finally: await c.aclose()
@pytest.mark.asyncio
async def test_stream_preserves_error_and_hides_raw_exception(harness,monkeypatch):
    from assistant_api.routers import matters as router
    async def failed(*args,**kwargs):
        return dict(outcome='failed',status='failed',local_run_id='local',workflow_execution_id='workflow',error=ERROR)
    monkeypatch.setattr(router.matters,'intake',failed)
    h=harness
    r=await h.client.post('/api/v1/matters/intake/stream',headers=auth(h.token_a),json={'text':'你好','idempotency_key':'known-error'})
    assert r.status_code==200
    data=json.loads(r.text.split('event: error\ndata: ')[1].split('\n\n')[0])
    assert data==dict(outcome='failed',status='failed',local_run_id='local',workflow_execution_id='workflow',error=ERROR)
    async def broken(*args,**kwargs): raise RuntimeError('PRIVATE_RAW_PROVIDER_BODY')
    monkeypatch.setattr(router.matters,'intake',broken)
    r=await h.client.post('/api/v1/matters/intake/stream',headers=auth(h.token_a),json={'text':'你好','idempotency_key':'unknown-error'})
    data=json.loads(r.text.split('event: error\ndata: ')[1].split('\n\n')[0])
    assert not data['error']['outcome_is_known'] and data['outcome']=='unknown'
    assert 'PRIVATE_RAW_PROVIDER_BODY' not in r.text
@pytest.mark.asyncio
async def test_history_reconciles_failure_with_owner_boundary(harness):
    from assistant_api.services import matter_plan
    h=harness;local=str(uuid4());calls=[]
    async with h.app.state.session_factory() as db:
        db.add(NexusRun(local_run_id=local,user_id=h.user_a,idempotency_key='history-error',request_text='你好',purpose=matter_plan.PURPOSE,nexus_run_id='run',workflow_execution_id='workflow',status='unknown',context_json={'text':'你好'}))
        await db.commit()
    async def upstream(req):
        calls.append(req)
        return httpx.Response(200,json=dict(workflow_execution_id='workflow',run_id='run',status='failed',error=ERROR))
    nexus=h.app.state.nexus_client;old=nexus._client
    nexus._client=httpx.AsyncClient(base_url='http://nexus.test',transport=httpx.MockTransport(upstream))
    try:
        r=await h.client.get('/api/v1/matters/intake/'+local,headers=auth(h.token_b))
        assert r.status_code==404 and not calls
        r=await h.client.get('/api/v1/matters/intake/'+local,headers=auth(h.token_a))
        assert r.status_code==200,r.text
        assert r.json()['outcome']=='failed' and r.json()['error']==ERROR
        assert all(req.method=='GET' for req in calls)
    finally: await nexus._client.aclose();nexus._client=old
