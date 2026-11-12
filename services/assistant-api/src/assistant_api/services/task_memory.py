"""Prepare one bounded owned-memory snapshot before starting an existing Nexus Agent.

Snapshots are task inputs in NexusRun JSON, not a second long-term fact store.
Only new chat tasks read automatically. All model decisions/continuation remain
with Nexus; this module makes no model request and never changes permissions.
"""
import asyncio
import copy
from datetime import UTC, datetime

import anyio
from sqlalchemy import update

from assistant_api.models import NexusRun
from assistant_api.nexus_client.errors import NexusError
from assistant_api.core.errors import AssistantError
from assistant_api.schemas.memory import BusinessRecallRequest
from assistant_api.services import business_memory, matter_plan

MAX_INPUT_MEMORY_BYTES = 32000


async def prefetch(client, user_id, flow, settings):
    """Fetch a small relevant snapshot, retaining unavailable, disabled and incomplete states distinctly."""
    snapshot = {'origin':'assistant_task_prefetch','status':'skipped','confirmed_empty':False,
                'cache_valid':False,'started_at':datetime.now(UTC).isoformat(),'result':None}
    if flow.get('trigger_source','chat') != 'chat':
        return {**snapshot,'reason':'no_new_user_message'}
    payload = BusinessRecallRequest(query=flow['text'],purpose='general',matter_id=flow.get('matter_id'),
                                    limit=settings.memory_prefetch_max_items)
    try:
        result = await asyncio.wait_for(business_memory.recall(client,user_id,payload,
            timezone=flow['timezone'],scan_limit=settings.memory_prefetch_scan_limit,
            max_result_bytes=MAX_INPUT_MEMORY_BYTES),timeout=settings.memory_prefetch_timeout_seconds)
    except TimeoutError:
        return {**snapshot,'status':'unavailable','error':{'code':'memory_prefetch_timeout','retryable':True}}
    except (NexusError,AssistantError) as exc:
        return {**snapshot,'status':'unavailable','error':{'code':exc.code,'retryable':getattr(exc,'retryable',False)}}
    return {**snapshot,'status':'disabled' if result['status']=='disabled' else 'completed',
            'confirmed_empty':result['confirmed_empty'],'cache_valid':True,'result':result,
            'retrieved_at':datetime.now(UTC).isoformat()}


async def initialize_task(db, request, user, row):
    """Fill the uniquely claimed local task before remote launch; replaying clients never perform this read.

    Cancelling this preparatory request records a known local failure rather than
    leaving a row that appears to be running a remote model indefinitely. An
    independently cancelled task is never relaunched after the lookup finishes.
    """
    identity, user_id = row.local_run_id, user.user_id
    flow = copy.deepcopy(row.context_json['api_flow'])
    try:
        snapshot = await prefetch(request.app.state.memory_store,user_id,flow,request.app.state.settings)
    except asyncio.CancelledError:
        # Starlette cancellation scopes cancel every await until shielded. Keep
        # this short local write protected so a cancelled HTTP request cannot
        # invalidate its connection halfway through recording the known result.
        with anyio.CancelScope(shield=True):
            await db.execute(update(NexusRun).where(NexusRun.local_run_id==identity,NexusRun.status=='pending',
                NexusRun.nexus_run_id.is_(None)).values(status='failed',error_code='memory_prefetch_interrupted',
                    completed_at=datetime.now(UTC)).execution_options(synchronize_session=False))
            await db.commit()
        raise
    await db.refresh(row)
    if row.status != 'pending' or row.nexus_run_id:
        return False
    old = copy.deepcopy(row.context_json)
    flow = copy.deepcopy(old['api_flow'])
    flow.update(memory_prefetch=snapshot,memory_sources=[item['source'] for item in
        (snapshot.get('result') or {}).get('items',[])],state='planning')
    context = {**old,'api_flow':flow,'memory_context':copy.deepcopy(snapshot)}
    claimed = await db.execute(update(NexusRun).where(NexusRun.local_run_id==identity,
        NexusRun.status=='pending',NexusRun.nexus_run_id.is_(None),NexusRun.context_json==old
        ).values(context_json=context,request_text=matter_plan.prepare_input(context)).execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(row)
    return claimed.rowcount == 1 and row.status == 'pending'


def cached_empty(flow):
    """Reuse a confirmed empty start-of-task snapshot, never an unavailable or partial lookup."""
    snapshot = flow.get('memory_prefetch') or {}
    if not (snapshot.get('status')=='completed' and snapshot.get('confirmed_empty') is True
            and snapshot.get('cache_valid') is True):
        return None
    result = copy.deepcopy(snapshot['result'])
    result.update(retrieval_method='task_snapshot',next_steps=[],
        cache={'scope':'current_task','retrieved_at':snapshot['retrieved_at']})
    return result


def invalidate(flow, reason):
    """Stop reusing the empty snapshot after the same Agent successfully saves a new fact."""
    snapshot = flow.get('memory_prefetch')
    if snapshot:
        snapshot['cache_valid'] = False
        snapshot['invalidated_by'] = reason
