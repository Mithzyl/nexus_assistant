"""Focused reproduction of finite Nexus event responses found during real record-use-case evaluation."""
import json
import httpx
import pytest
from assistant_api.nexus_client import NexusPilotClient
from assistant_api.nexus_client.models import WorkflowCreateRequest


def frame(sequence, status):
    """Build a committed public event without model/provider content."""
    return f'id: {sequence}\nevent: agent.workflow.{status}\ndata: '+json.dumps({'workflow_execution_id': 'workflow-test', 'workflow_status': status, 'event_sequence': sequence})+'\n\n'


@pytest.mark.asyncio
async def test_finite_start_stream_reads_followup_events_without_starting_again():
    """A finite POST response is followed by GET cursor reads until the same workflow completes."""
    calls = []
    async def handle(request):
        """Return acceptance first and then persisted completion on a read-only request."""
        calls.append((request.method, request.url.path, request.headers.get('last-event-id')))
        if request.method == 'POST':
            return httpx.Response(200, text=frame(1, 'running'), headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, text=frame(2, 'completed'), headers={'content-type': 'text/event-stream'})
    client = NexusPilotClient(base_url='http://nexus.test', application_id='personal_assistant', application_token='test-token-not-real', transport=httpx.MockTransport(handle))
    payload = WorkflowCreateRequest(idempotency_key='finite-stream-001', role_bindings={'controller': {'provider': 'test', 'model': 'test'}})
    try:
        events = [event async for event in client.start_workflow_events('isolated-user', 'existing-run', payload)]
    finally:
        await client.aclose()
    assert [event.id for event in events] == ['1', '2']
    assert calls == [('POST', '/api/v1/app/runs/existing-run/agent-workflows', None), ('GET', '/api/v1/app/agent-workflows/workflow-test/events', '1')]
