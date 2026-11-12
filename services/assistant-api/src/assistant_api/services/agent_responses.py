"""Execute caller bodies through existing business routes, retaining ambiguous outcomes.

The receipt and per-run claim commit before dispatch. Business routes own their
transactions. A lost response leaves an unknown receipt and never triggers a
second dispatch, even when a model proposes a new execution identifier.
"""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from assistant_api.core.errors import ConflictError, ForbiddenError, NotFoundError
from assistant_api.models import AgentResponseReceipt, NexusRun, User
from assistant_api.services.api_operations import OperationCall, OperationCatalog, OperationDecision, execute_call, resolve_result_references
from assistant_api.services import response_cards


def outcome(execution_id, status, message=None, *, result=None, code=None):
    """Build only the exact supported Nexus response fields and combinations."""
    return {'protocol_version': '1', 'execution_id': execution_id,
            'disposition': 'stop' if status in {'unknown', 'partially_succeeded'} else 'continue',
            'execution_status': status, 'result': result,
            'error': None if status == 'succeeded' else {
                'code': code or status, 'message': (message or '本次未执行。')[:2000],
                'retryable': status == 'failed'}}


def semantic_call(value):
    """Compare repeat proposals ignoring request keys/summary and equivalent time offsets."""
    if isinstance(value, dict):
        return {k: semantic_call(v) for k, v in value.items()
                if k.lower() not in {'idempotency_key', 'idempotency-key', 'summary'}}
    if isinstance(value, list):
        return [semantic_call(v) for v in value]
    if isinstance(value, str):
        if 'T' in value:
            try:
                instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if instant.tzinfo:
                    return instant.astimezone(UTC).isoformat()
            except ValueError:
                pass
        return value.strip()
    return value


def parse_proposal(text):
    """Accept a complete JSON tool proposal inside a single model code fence, retaining original audit text.

    Final answers remain raw JSON because Nexus validates their original body.
    This removes only a known presentation wrapper, never repairs JSON, guesses
    arguments or accepts trailing natural-language instructions.
    """
    value=text.strip()
    if value.startswith('```'):
        first, separator, rest=value.partition('\n')
        if not separator or first not in {'```','```json'}:
            raise ValueError('只返回一个完整 JSON 对象，不要代码围栏或解释文字。')
        value=rest.strip()
        if value.endswith('```'):
            value=value[:-3].strip()
        parsed=OperationDecision.model_validate_json(value).model_dump(mode='json',exclude_none=True)
        if parsed['decision'] in {'answer','ask_user','clarify'}:
            raise ValueError('终答必须直接返回原始 JSON 对象，首字符为 {，不要 ```json 代码围栏。')
        return parsed
    return OperationDecision.model_validate_json(value).model_dump(mode='json',exclude_none=True)


def final_projection(text, flow):
    """Project optional cards, text and choices without forcing a presentation or executing business operations."""
    decision = OperationDecision.model_validate_json(text).model_dump(mode='json', exclude_none=True)
    pending = {'execution_result': flow['visible_result']} if flow.get('visible_result') else {}
    if decision['decision'] == 'answer':
        pending['cards'] = response_cards.prepare_cards(decision.get('cards', []), flow.get('visible_result'), results=flow.get('results'))
        return {'outcome': 'answer', 'answer': decision['answer'], 'clarify': None, 'pending_context': pending}
    if decision['decision'] in {'ask_user', 'clarify'}:
        cards=decision.get('cards', [])
        pending['cards']=response_cards.prepare_cards(cards,flow.get('visible_result'),results=flow.get('results'))
        return {'outcome': 'needs_clarification', 'answer': None, 'clarify': decision['question'],
                'pending_context': {**pending, 'choices': decision.get('choices', [])}}
    raise ValueError('终答只能是answer或ask_user。')


def validation_feedback(error, stage):
    """Explain how to repair one model message without exposing raw invalid values or replaying prior writes."""
    if isinstance(error, ValidationError):
        issues = []
        for item in error.errors(include_input=False, include_url=False)[:5]:
            path = '.'.join(map(str, item['loc'])) or '$'
            code = item['type']
            context = item.get('ctx') or {}
            if code == 'json_invalid':
                detail = '必须是完整有效的 JSON 对象，使用双引号，无代码围栏或附加文字'
            elif code == 'value_error':
                # Our own model validators emit fixed, input-free instructions.
                detail = str(context.get('error') or '字段组合不符合当前决策定义')
            elif code in {'union_tag_invalid', 'union_tag_not_found'}:
                detail = 'type 必须选择 ' + str(context.get('expected_tags') or '已发布的区块类型')
            elif code == 'missing':
                detail = '缺少必填字段'
            elif code == 'extra_forbidden':
                detail = '接口未定义此字段，请删除'
            else:
                detail = code
                limits = {name: context[name] for name in (
                    'expected', 'min_length', 'max_length', 'ge', 'gt', 'le', 'lt'
                ) if name in context}
                if limits:
                    detail += '，要求 ' + json.dumps(limits, ensure_ascii=False)
            issues.append(f'{path[:180]}: {detail[:350]}')
        message = '; '.join(issues)
    elif isinstance(error, RecursionError):
        message = 'JSON 或结果引用嵌套过深，请减少层级。'
    else:
        message = str(error)[:700]
    suffix = ('卡片需使用 title 和 blocks，区块 type 为 text、fields、image 或 timeline；'
              'result 卡片的业务字段使用真实成功结果引用，未保存不得展示为已完成。'
              if stage == 'cards' else
              'call 提供 operation_id 和接口规定的完整参数；answer 提供 answer；'
              'ask_user 提供 question，选项放 choices；search 提供 search_query。')
    return (f'{stage} 校验失败：{message}。本条输出未新增业务执行；此前成功操作保留，'
            f'请在原 Agent 中只修正本条输出，不要重做已成功操作。{suffix}')[:1900]


async def finish(db, root, receipt, flow, response):
    """Save the exact response and flow atomically, preserving concurrent terminal status."""
    await db.refresh(root)
    context = copy.deepcopy(root.context_json)
    context['api_flow'] = flow
    if response.get('execution_status') in {'rejected', 'unknown', 'partially_succeeded'}:
        context['body_processing_stopped'] = True
    context['body_processing_execution_id'] = receipt.execution_id if response.get('execution_status') == 'unknown' else None
    root.context_json = context
    receipt.status = 'unknown' if response.get('execution_status') == 'unknown' else 'completed'
    receipt.response_json = response
    await db.commit()
    return response


def supplied_definitions(context, receipts):
    """Read definitions from this Run's initial message and successful returned tool messages only.

    Cached names are not delivery evidence. Nexus appends successful callback
    results to the same Agent before continuing; no other Run's records qualify.
    Later returned definitions/components supersede the same names in older messages.
    """
    definitions = {item['operation_id']: item for item in context.get('api_operations', [])}
    schemas = dict((context.get('components') or {}).get('schemas', {}))
    for receipt in receipts:
        response = receipt.response_json or {}
        if response.get('execution_status') != 'succeeded':
            continue
        result = response.get('result') or {}
        if result.get('operation_id') == 'resolve_operation_definitions':
            data = result.get('data') or {}
        elif result.get('operation_id') == 'search':
            data = result
        else:
            continue
        definitions.update({item['operation_id']: item for item in data.get('api_operations', [])})
        schemas.update((data.get('components') or {}).get('schemas', {}))
    return definitions, schemas


def definition_matches(operation, definitions, schemas):
    """Compare exact published structures with supplied messages, without hashes or semantic inference."""
    return (operation is not None
            and definitions.get(operation.operation_id) == operation.model_description(include_components=False)
            and all(schemas.get(name) == schema for name, schema in operation.components.items()))


async def process_response(db, app, payload):
    """Check callback ownership, dispatch business calls and retain their memory sources."""
    identity = payload['execution_id']
    if payload['application_id'] != app.state.settings.nexus_application_id:
        raise ForbiddenError('application_mismatch', '应用标识与凭据不匹配。')
    user = await db.scalar(select(User).where(User.user_id == payload['external_user_id'], User.status == 'active'))
    root = await db.scalar(select(NexusRun).where(NexusRun.nexus_run_id == payload['run_id'], NexusRun.user_id == payload['external_user_id']))
    if user is None or root is None or not (root.context_json or {}).get('caller_processed'):
        raise NotFoundError('response_run_not_found', '运行不存在或不允许正文处理。')
    if root.workflow_execution_id and root.workflow_execution_id != payload['workflow_id']:
        return outcome(identity, 'rejected', '工作流与保存的运行不匹配。', code='workflow_mismatch')
    existing = await db.get(AgentResponseReceipt, identity)
    if existing:
        if existing.request_json != payload:
            raise ConflictError('response_execution_conflict', '同一处理编号不能用于不同请求。')
        return existing.response_json or outcome(identity, 'unknown', '原处理结果尚未确认，禁止重复执行。')
    if root.status not in {'pending', 'running'}:
        return outcome(identity, 'rejected', '运行已停止，不执行新的业务操作。', code='run_stopped')
    old = copy.deepcopy(root.context_json)
    if old.get('body_processing_stopped'):
        return outcome(identity, 'rejected', '运行的业务处理已停止，不能用新编号绕过。', code='processing_stopped')
    if old.get('body_processing_execution_id'):
        return outcome(identity, 'unknown', '原业务处理尚未确认，禁止新调用。')
    context = copy.deepcopy(old)
    context['body_processing_execution_id'] = identity
    claimed = await db.execute(update(NexusRun).where(
        NexusRun.local_run_id == root.local_run_id, NexusRun.context_json == old,
        NexusRun.status.in_(['pending', 'running']),
    ).values(context_json=context, workflow_execution_id=payload['workflow_id']).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await db.rollback()
        return outcome(identity, 'unknown', '运行正在处理其他请求，请核对原结果。')
    receipt = AgentResponseReceipt(execution_id=identity, local_run_id=root.local_run_id,
                                   response_id=payload['response_id'], request_json=payload, status='processing')
    db.add(receipt)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.get(AgentResponseReceipt, identity)
        if existing and existing.request_json == payload:
            return existing.response_json or outcome(identity, 'unknown', '原处理结果尚未确认。')
        raise ConflictError('response_execution_conflict', '响应编号已用于其他请求。') from exc
    flow = copy.deepcopy(old['api_flow'])
    catalog = OperationCatalog(app)
    validation_stage = 'decision'
    try:
        decision = parse_proposal(payload['content'])
        kind = decision['decision']
        if kind in {'answer', 'ask_user', 'clarify'}:
            validation_stage = 'cards'
            final_projection(payload['content'], flow)
            return await finish(db, root, receipt, flow, {'protocol_version': '1', 'execution_id': identity, 'disposition': 'complete'})
        if kind == 'search':
            from assistant_api.services.operation_intake import model_context
            context = model_context(flow, catalog, catalog_query=decision['search_query'],
                                    catalog_offset=decision.get('search_offset', 0), initial_message=False)
            return await finish(db, root, receipt, flow, outcome(identity, 'succeeded', result={
                'operation_id': 'search', 'operation_index': context['operation_index'],
                'api_operations': context['api_operations'], 'components': context['components'],
                'catalog_feedback': context['catalog_feedback']}))
        validation_stage = 'call_parameters'
        resolved = resolve_result_references(decision['call'], flow.get('visible_result'), results=flow.get('results'))
        proposed_call = OperationCall.model_validate(resolved)
        prior = list(await db.scalars(select(AgentResponseReceipt).where(
            AgentResponseReceipt.local_run_id == root.local_run_id,
            AgentResponseReceipt.status == 'completed').order_by(AgentResponseReceipt.created_at, AgentResponseReceipt.execution_id)))
        definitions, supplied_schemas = supplied_definitions(old, prior)
        indexed = flow.get('catalog_mode') == 'indexed'
        if (indexed and proposed_call.operation_id not in definitions) or (
                not indexed and proposed_call.operation_id not in flow['offered_operations']):
            return await finish(db,root,receipt,flow,outcome(identity,'failed',
                '该接口尚未加载完整定义。请从operation_index选择uri，调用resolve_operation_definitions读取参数后再决定调用；目录不足时decision=search。',code='operation_definition_required'))
        published = catalog.operations.get(proposed_call.operation_id)
        # The fixed read-only resolver must remain reachable when shared error
        # response schemas change, otherwise stale business definitions could
        # never be reloaded. Its request is still validated against current schema.
        if (indexed and proposed_call.operation_id != 'resolve_operation_definitions'
                and not definition_matches(published, definitions, supplied_schemas)):
            uri = published.uri if published else definitions[proposed_call.operation_id].get('uri')
            return await finish(db, root, receipt, flow, outcome(identity, 'failed',
                '接口定义已失效，本条调用尚未执行。请重新解析该uri；若已从目录移除，查询当前索引。'
                '其他未变化的定义继续有效，不重做已成功操作。', code='operation_definition_stale',
                result={'operation_id': proposed_call.operation_id, 'uri': uri, 'formal_write_status': 'not_started'}))
        operation, call = catalog.validate_call(proposed_call)
    except (ValueError, RecursionError) as exc:
        return await finish(db, root, receipt, flow, outcome(identity, 'failed',
            validation_feedback(exc, validation_stage), code='invalid_parameters'))
    if flow.get('trigger_source') == 'follow_up':
        from assistant_api.models import FollowUp, FollowUpExecution
        from assistant_api.services.follow_ups import execution_is_current
        execution = await db.get(FollowUpExecution, flow.get('follow_up_execution_id'))
        rule = await db.get(FollowUp, flow.get('follow_up_id'))
        if (not execution or not rule or rule.user_id != user.user_id
            or not await execution_is_current(db, flow['request_key'], lease_owner=flow.get('lease_owner'))
            or (rule.execution_mode == 'evaluate' and operation.effect == 'write')):
            return await finish(db, root, receipt, flow, outcome(identity, 'rejected', '跟进规则未授权这项业务操作。', code='approval_required'))
    if operation.effect != 'read' and any(
        item.executed_call_json and (item.response_json or {}).get('execution_status') == 'succeeded'
        and semantic_call(item.executed_call_json) == semantic_call(call.model_dump(mode='json')) for item in prior
    ):
        return await finish(db, root, receipt, flow, outcome(identity, 'rejected', '本次任务已成功执行相同操作，不重复创建。', code='duplicate_operation'))
    # Only the actual dispatch gets a server-owned key; original model text stays intact.
    actual = call.model_copy(deep=True)
    dispatch_key = 'body-' + root.local_run_id + '-' + str(flow['step'])
    if actual.body is not None and 'idempotency_key' in actual.body:
        actual.body['idempotency_key'] = dispatch_key
    actual.headers = {k: v for k, v in actual.headers.items() if k.lower() != 'idempotency-key'}
    receipt.executed_call_json = actual.model_dump(mode='json')
    receipt.status = 'dispatching'
    await db.commit()
    await db.refresh(root)
    if root.status not in {'pending', 'running'}:
        return await finish(db, root, receipt, flow, outcome(identity, 'rejected', '运行已取消，不再调用业务接口。', code='run_stopped'))
    try:
        business = await asyncio.wait_for(execute_call(app, '', operation, actual, dispatch_key,
            execution_user_id=user.user_id, execution_run_id=root.local_run_id), timeout=60)
    except (TimeoutError, httpx.HTTPError):
        return await finish(db, root, receipt, flow, outcome(identity, 'unknown', '业务结果未确认，请核对原记录，不要重复提交。'))
    if operation.operation_id == 'resolve_operation_definitions' and business['status'] == 'succeeded':
        # Definitions are read-only tool results, never authorization to mutate.
        # Deduplicate only what this Agent was actually given, not model claims.
        data = business['data']
        loaded = data['api_operations']
        unchanged = {item['operation_id'] for item in loaded
                     if definition_matches(catalog.operations[item['operation_id']], definitions, supplied_schemas)}
        data['already_loaded'] = [item['uri'] for item in loaded if item['operation_id'] in unchanged]
        data['updated_uris'] = [item['uri'] for item in loaded
                               if item['operation_id'] in definitions and item['operation_id'] not in unchanged]
        data['api_operations'] = [item for item in loaded if item['operation_id'] not in unchanged]
        data['components']['schemas'] = {name: schema for name, schema in data['components']['schemas'].items()
                                         if supplied_schemas.get(name) != schema}
        flow['offered_operations'] = list(dict.fromkeys([*flow.get('offered_operations', []),
                                                        *(item['operation_id'] for item in loaded)]))
        flow['provided_schema_names'] = list(dict.fromkeys([*flow.get('provided_schema_names', []),
                                                           *data['components']['schemas']]))
        data['guidance'] = ('定义已提供；自行决定业务调用、获取其他定义或回答。相同完整定义在本Agent继续有效，不重复解析。'
                            '本次返回的更新定义及共享结构替代同名旧说明；参数错误本身不要求重取未变化的定义。')
    flow.setdefault('results', {})[str(flow['step'])] = copy.deepcopy(business)
    flow['visible_result'] = business
    flow['step'] += 1
    if business['status'] == 'succeeded' and isinstance(business.get('data'), (dict, list)):
        if operation.operation_id in {'save_business_memory_fact','mutate_business_memory'}:
            from assistant_api.services.task_memory import invalidate
            invalidate(flow,operation.operation_id)
            changed=business['data'].get('entry_id')
            if changed:
                flow['memory_sources']=[source for source in flow.get('memory_sources',[]) if source['entry_id']!=changed]
        if operation.operation_id == 'recall_business_memory':
            dependencies = {source['entry_id']: source for source in flow.get('memory_sources', [])}
            for item in business['data'].get('items', []):
                dependencies[item['entry_id']] = item['source']
            flow['memory_sources'] = list(dependencies.values())
        result = {**business, 'executed_arguments': actual.body, 'executed_call': actual.model_dump(mode='json'),
                  'proposed_summary': call.summary}
        flow['receipts'].append(result)
        return await finish(db, root, receipt, flow, outcome(identity, 'succeeded', result=result))
    if business['http_status'] in {401, 403, 404}:
        return await finish(db, root, receipt, flow, outcome(identity, 'rejected', '业务对象不存在或无权执行。', code='business_access_denied'))
    error=(business.get('data') or {}).get('error',{}) if isinstance(business.get('data'),dict) else {}
    if (operation.operation_id == 'resolve_operation_definitions' and business['http_status'] == 422
            and error.get('code') in {'operation_uri_invalid', 'operation_definitions_too_large', 'invalid_request'}):
        return await finish(db, root, receipt, flow, outcome(identity, 'failed',
            error.get('message') or '索引参数无效，请按已发布uri修正；没有执行业务操作。',
            code=error.get('code')))
    if (operation.operation_id=='resolve_business_preferences' or (
        operation.path in {'/api/v1/schedules','/api/v1/commute/estimate','/api/v1/todos',
                          '/api/v1/todos/{todo_id}','/api/v1/schedules/{schedule_id}',
                          '/api/v1/matters/drafts','/api/v1/matters/drafts/{draft_id}',
                          '/api/v1/matters/commute-drafts','/api/v1/matters/drafts/{draft_id}/commute-quote',
                          '/api/v1/matters/drafts/{draft_id}/confirm',
                          '/api/v1/commute-plans','/api/v1/commute-plans/{plan_id}'}
        and business['http_status'] in {404,409,422}
        and error.get('code') in {'input_source_conflict','preference_selection_unresolved','preference_task_unavailable',
                                 'preferences_draft_changed','preferences_verification_required',
                                 'business_memory_changed','business_memory_disabled','business_memory_unconfirmed',
                                 'schedule_defaults_unresolved','schedule_time_required',
                                 'memory_reference_changed','memory_reference_not_found',
                                 'memory_not_found','commute_plan_fields_invalid','commute_draft_fields_invalid',
                                 'commute_quote_fields_invalid','memory_fields_invalid'})):
        # These failures precede formal writes. The same Agent can recall updated
        # memory or ask for a choice; an actual timeout remains unknown above.
        return await finish(db,root,receipt,flow,outcome(identity,'failed',
            error.get('message') or '所选记忆尚未用于业务保存，请补查或询问用户。',
            code=error.get('code') or 'preference_selection_unresolved'))
    if operation.operation_id == 'recall_business_memory' or (
        operation.operation_id in {'save_business_memory_fact','mutate_business_memory'}
        and business['http_status'] in {409, 422}
        and isinstance(business.get('data'), dict)
        and (business['http_status']==422 or
             (business['data'].get('error') or {}).get('code', '').startswith(('memory_', 'business_memory_')))
    ):
        # Recall has no business side effects. Documented fact validation failures
        # happen before publishing a memory, so their actual failure is actionable.
        error = (business.get('data') or {}).get('error') or {}
        return await finish(db, root, receipt, flow, outcome(identity, 'failed',
            error.get('message') or '记忆字段校验未通过，未执行写入，请按接口结构补齐。',
            code=error.get('code') or 'memory_unavailable'))
    # A business handler may have committed before an HTTP error. No generic 4xx/5xx
    # response proves rollback; only our pre-dispatch validation is retryable.
    return await finish(db, root, receipt, flow, outcome(identity, 'unknown', '业务接口未返回确定成功结果，请核对原记录。'))
