"""Persist the Assistant-owned API planning, execution and reply loop.

Uses existing NexusRun records and matter_plan prompts. Only current user input,
bounded related historical messages, selected public API definitions and minimal
execution receipts reach Nexus. Business response bodies remain local for cards.
No model URL, Python code or model-authored identity is executed.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select, update, or_
import httpx

from assistant_api.core.errors import AssistantError, ConflictError, NotFoundError
from assistant_api.models import NexusRun
from assistant_api.services import ai_tasks, matter_plan, run_service, response_cards
from assistant_api.services.api_operations import OperationCatalog, execute_call, model_receipt, search_terms, resolve_result_references

MAX_STEPS = 8
logger = logging.getLogger(__name__)


def response_for(row: NexusRun) -> dict:
    """Return the durable public result without dispatching any operation on reads."""
    resolution = copy.deepcopy((row.context_json or {}).get('unified_intake_result') or {})
    # Old pending calls remain read-only history; opening a page cannot dispatch them.
    if resolution.get('outcome') == 'needs_confirmation':
        resolution.update(outcome='needs_clarification', clarify='这条旧请求还没有执行。请重新告诉我你想怎么改。')
        resolution.setdefault('pending_context', {}).pop('operation', None)
    return {'local_run_id': row.local_run_id, 'parent_run_id': (row.context_json or {}).get('parent_run_id'),
            'status': row.status, 'workflow_execution_id': row.workflow_execution_id,
            'intent': 'question', 'outcome': row.status if row.status in {'unknown', 'failed', 'cancelled'} else 'processing', 'answer': None, 'clarify': None, 'pending_context': {}, **resolution}


async def save_result(db, row: NexusRun, flow: dict, outcome: str, **fields) -> dict:
    """Atomically store flow state and its existing unified-history projection."""
    context = copy.deepcopy(row.context_json or {})
    context['api_flow'] = copy.deepcopy(flow)
    pending = dict(fields.get('pending_context') or {})
    if flow.get('visible_result') is not None:
        pending.setdefault('execution_result', copy.deepcopy(flow['visible_result']))
    fields['pending_context'] = pending
    context['unified_intake_result'] = {'outcome': outcome, 'intent': 'question', **fields}
    row.context_json = context
    await db.commit()
    return response_for(row)


async def message_history(db, user, text: str, continuation_run_id: str | None) -> list[dict]:
    """Retrieve related user-visible messages only; never scan business tables for model input."""
    rows = list(await db.scalars(select(NexusRun).where(
        NexusRun.user_id == user.user_id, NexusRun.purpose == matter_plan.PURPOSE,
    ).order_by(NexusRun.created_at.desc()).limit(30)))
    if continuation_run_id and not any(r.local_run_id == continuation_run_id for r in rows):
        parent = await db.scalar(select(NexusRun).where(NexusRun.local_run_id == continuation_run_id, NexusRun.user_id == user.user_id, NexusRun.purpose == matter_plan.PURPOSE))
        if parent is None:
            raise NotFoundError('intake_result_not_found', '原消息不存在或无权使用。')
        rows.insert(0, parent)
    terms = search_terms(text)
    selected = []
    for row in rows:
        context = row.context_json or {}
        if context.get('api_internal'):
            continue
        message = context.get('text', '')
        if row.local_run_id != continuation_run_id and not (terms & search_terms(message)):
            continue
        saved = context.get('unified_intake_result') or {}
        # This is a historical message reference, not a fresh claim about current
        # database state. Old execution references help preserve object identity.
        selected.append({'message': message[:1200], 'answer': (saved.get('answer') or saved.get('clarify') or '')[:1600],
                         'references': (context.get('api_flow') or {}).get('receipts', [])[-2:],
                         'matter_id': saved.get('matter_id'), 'draft_id': saved.get('draft_id')})
        if len(selected) >= 5:
            break
    return list(reversed(selected))


async def ask_model(db, request, user, context: dict, key: str):
    """Create each JSON planning turn once; concurrent replays never launch another workflow."""
    row, replayed = await run_service.create_ai_task(db, user=user, purpose=matter_plan.PURPOSE,
        trigger=ai_tasks.TRIGGER_CHAT, idempotency_key=key, context=context,
        attach=lambda record: setattr(record, 'context_json', copy.deepcopy(context)),
        strict_input_match=False)
    if replayed:
        if (row.context_json or {}).get('text') != context['text']:
            raise ConflictError('idempotency_conflict', '同一请求标识已用于其他输入。')
        return row, None
    if row.status not in run_service.TERMINAL_STATUSES:
        row = await run_service.launch_ai_task(db, request.app.state.nexus_client, request.app.state.settings, request.app.state.session_factory, user=user, row=row)
        row, timed_out = await run_service.wait_terminal(db, request.app.state.nexus_client, user=user, row=row, timeout_seconds=request.app.state.settings.matter_ai_timeout_seconds)
        if timed_out:
            raise AssistantError('api_plan_timeout', '尚未取得助手结果，请查询原请求。', 504)
    if row.status != 'succeeded':
        raise AssistantError('api_plan_failed', '助手本次未能形成调用，请保留原消息。', 502)
    try:
        decision = matter_plan.validate_result(run_service.answer_text(row), context)
    except ai_tasks.AiTaskResultRejected as exc:
        raise AssistantError('api_plan_invalid', '助手返回的调用描述未通过校验，未执行操作。', 422) from exc
    return row, decision


def model_context(flow: dict, catalog: OperationCatalog) -> dict:
    """Compose selected interface input schemas plus allowed messages, excluding response bodies."""
    # Newly returned reference names (for example draft_id) identify the next
    # interface family without a hand-maintained per-operation workflow table.
    reference_names = set()
    for historical in flow['history']:
        reference_names.update(key for key in ('matter_id', 'draft_id', 'selected_matter_id') if historical.get(key))
    for receipt in flow['receipts'][-2:]:
        refs = receipt.get('references') or {}
        reference_names.update(refs)
        if isinstance(refs.get('draft'), dict):
            reference_names.update(refs['draft'])
    selected = catalog.search(flow['query'] + ' ' + ' '.join(sorted(reference_names)))
    flow['offered_operations'] = [op.operation_id for op in selected]
    return {'text': flow['text'], 'now': flow['now'], 'timezone': flow['timezone'],
            'history': flow['history'], 'api_operations': [op.model_description() for op in selected],
            'execution_results': flow['receipts'], 'card_schema': response_cards.presentation_schema(),
            'idempotency_key': flow['request_key'], 'api_internal': flow['step'] > 0}


async def intake(request: Request, db, user, *, text: str, idempotency_key: str,
                 continuation_run_id: str | None = None, matter_id: str | None = None,
                 memory_disclosures: list | None = None) -> dict:
    """Start the API-description flow, or return a saved operation without redispatch."""
    if memory_disclosures:
        raise AssistantError('api_context_scope', '本次接口调用只使用当前输入与相关历史消息，请取消额外记忆选择。', 422)
    key = 'matter-intake:' + idempotency_key
    existing = await db.scalar(select(NexusRun).where(NexusRun.user_id == user.user_id, NexusRun.idempotency_key == key))
    if existing is not None:
        if (existing.context_json or {}).get('text') != text:
            raise ConflictError('idempotency_conflict', '同一请求标识已用于其他输入。')
        if 'api_flow' in (existing.context_json or {}):
            return response_for(existing)
        from assistant_api.services import matters
        return await matters.get_intake_result(db, user, existing.local_run_id, client=request.app.state.nexus_client)
    history = await message_history(db, user, text, continuation_run_id)
    if matter_id:
        # A route selected by the user is an explicit local reference, not bulk
        # content from the matter or its schedules.
        history.append({'selected_matter_id': matter_id})
    from assistant_api.services.matters import user_timezone
    timezone = await user_timezone(db, user, request.app.state.settings.matter_default_timezone)
    flow = {'text': text, 'query': text, 'now': datetime.now(UTC).isoformat(), 'timezone': timezone,
            'history': history, 'receipts': [], 'step': 0, 'state': 'planning', 'request_key': idempotency_key}
    catalog = OperationCatalog(request.app)
    context = model_context(flow, catalog)
    row, decision = await ask_model(db, request, user, context, key)
    if decision is None:
        return response_for(row)
    if continuation_run_id:
        context = dict(row.context_json or {})
        context['parent_run_id'] = continuation_run_id
        row.context_json = context
        await db.commit()
    return await advance(db, request, user, row, flow, catalog, decision)


async def advance(db, request, user, root: NexusRun, flow: dict, catalog: OperationCatalog, decision: dict) -> dict:
    """Execute explicit intent without extra approval and attach validated model-authored cards."""
    while flow['step'] < MAX_STEPS:
        kind = decision['decision']
        pending = {'execution_result': flow.get('visible_result')} if flow.get('visible_result') else {}
        if kind == 'answer':
            flow['state'] = 'completed'
            try:
                pending['cards'] = response_cards.prepare_cards(decision.get('cards', []), flow.get('visible_result'))
            except ValueError:
                logger.warning('api_card_invalid run=%s', root.local_run_id)
                pending['cards'] = []
            return await save_result(db, root, flow, 'answer', answer=decision['answer'], pending_context=pending)
        if kind in {'clarify', 'ask_user'}:
            flow['state'] = 'clarification'
            return await save_result(db, root, flow, 'needs_clarification', clarify=decision['question'], pending_context={**pending, 'choices': decision.get('choices', [])})
        if kind == 'search':
            flow['query'] = decision['search_query']
        else:
            try:
                resolved = resolve_result_references(decision['call'], flow.get('visible_result'))
            except ValueError as exc:
                flow['state'] = 'clarification'
                return await save_result(db, root, flow, 'needs_clarification', clarify=str(exc), pending_context=pending)
            try:
                operation, call = catalog.validate_call(resolved)
                if operation.operation_id not in flow['offered_operations']:
                    raise ValueError('必须先取得该接口定义')
            except ValueError as exc:
                logger.warning('api_call_rejected run=%s operation=%s offered=%s rule=%s', root.local_run_id,
                    decision.get('call', {}).get('operation_id'), flow['offered_operations'], str(exc))
                flow['state'] = 'rejected'
                await save_result(db, root, flow, 'failed', answer=None)
                raise AssistantError('api_call_invalid', str(exc), 422) from exc
            flow['call'] = call.model_dump(mode='json')
            receipt = await perform(db, request, root, flow, operation, call)
            if receipt is None:
                return response_for(root)
            flow['receipts'].append(model_receipt(receipt))
        flow['step'] += 1
        if flow['step'] >= MAX_STEPS:
            break
        flow['state'] = 'planning'
        await save_result(db, root, flow, 'processing', pending_context=pending)
        context = model_context(flow, catalog)
        _, decision = await ask_model(db, request, user, context, f'api-step:{root.local_run_id}:{flow["step"]}')
        if decision is None:
            return response_for(root)
    flow['state'] = 'limit_reached'
    return await save_result(db, root, flow, 'needs_clarification', clarify='本次已执行到可核对的步骤，请查看结果并说明下一步。', pending_context={'execution_result': flow.get('visible_result')})


async def perform(db, request, root, flow, operation, call) -> dict | None:
    """Persist dispatch intent; ambiguous interruption stays unknown and is never automatically retried."""
    # Persist an atomic per-step claim before HTTP, including read/preview calls.
    # A duplicate input or another API process cannot dispatch the same step again.
    flow['state'] = 'executing'
    context = copy.deepcopy(root.context_json or {})
    flow['last_dispatched_step'] = flow['step']
    context['api_flow'] = copy.deepcopy(flow)
    context['unified_intake_result'] = {'outcome': 'processing', 'intent': 'question', 'pending_context': {}}
    last_step = NexusRun.context_json['api_flow']['last_dispatched_step'].as_integer()
    claimed = await db.execute(update(NexusRun).where(
        NexusRun.local_run_id == root.local_run_id,
        or_(last_step.is_(None), last_step < flow['step']),
    ).values(context_json=context).execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(root)
    if claimed.rowcount != 1:
        return None
    try:
        receipt = await asyncio.wait_for(execute_call(request.app, request.headers.get('authorization', ''), operation, call, f'api-call-{root.local_run_id}-{flow["step"]}'), timeout=60)
    except (TimeoutError, httpx.HTTPError):
        flow['state'] = 'unknown'
        await save_result(db, root, flow, 'unknown')
        raise AssistantError('api_execution_unknown', '本次执行结果尚未确认，请核对原结果，不要重复提交。', 504)
    flow['visible_result'] = receipt
    logger.info('api_call_result run=%s operation=%s http_status=%s status=%s', root.local_run_id,
                operation.operation_id, receipt['http_status'], receipt['status'])
    flow['state'] = 'executed'
    await save_result(db, root, flow, 'processing', pending_context={'execution_result': receipt})
    return receipt
