"""Create one Nexus Agent for an Assistant task and project its validated result.

Uses existing NexusRun records and matter_plan prompts. Only current user input,
bounded historical messages, selected public
API definitions and actual scoped business results reach Nexus. Saved results supply local cards.
No model URL, Python code or model-authored identity is executed.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request
from sqlalchemy import select

from assistant_api.core.errors import AssistantError, ConflictError, NotFoundError
from assistant_api.models import NexusRun
from assistant_api.services.execution_context import ExecutionContext
from assistant_api.services import ai_tasks, matter_plan, run_service, response_cards
from assistant_api.services.api_operations import OperationCatalog, OperationDecision, search_terms



def response_for(row: NexusRun) -> dict:
    """Return the durable public result without dispatching any operation on reads."""
    if (row.context_json or {}).get('caller_processed'):
        from assistant_api.services.agent_responses import final_projection
        flow = row.context_json['api_flow']
        result = {'source': flow.get('trigger_source', 'chat'), 'matter_id': flow.get('matter_id'),
                  'follow_up_id': flow.get('follow_up_id'), 'follow_up_execution_id': flow.get('follow_up_execution_id'),
                  'local_run_id': row.local_run_id, 'parent_run_id': row.context_json.get('parent_run_id'),
                  'status': row.status, 'workflow_execution_id': row.workflow_execution_id, 'intent': 'question',
                  'outcome': 'processing', 'answer': None, 'clarify': None,
                  'pending_context': {'execution_result': flow['visible_result']} if flow.get('visible_result') else {}}
        if row.status == 'succeeded':
            try:
                result.update(final_projection(run_service.answer_text(row) or '', flow))
            except ValueError:
                result.update(outcome='failed', error={'code': 'final_output_invalid', 'message': '最终结果格式无效。', 'retryable': False})
        elif row.status in {'failed', 'cancelled', 'unknown'}:
            result['outcome'] = row.status
        return result
    resolution = copy.deepcopy((row.context_json or {}).get('unified_intake_result') or {})
    # Old pending calls remain read-only history; opening a page cannot dispatch them.
    if resolution.get('outcome') == 'needs_confirmation':
        resolution.update(outcome='needs_clarification', clarify='这条旧请求还没有执行。请重新告诉我你想怎么改。')
        resolution.setdefault('pending_context', {}).pop('operation', None)
    flow = (row.context_json or {}).get('api_flow') or {}
    return {'source': flow.get('trigger_source','chat'), 'matter_id': flow.get('matter_id'), 'follow_up_id': flow.get('follow_up_id'), 'follow_up_execution_id': flow.get('follow_up_execution_id'), 'local_run_id': row.local_run_id, 'parent_run_id': (row.context_json or {}).get('parent_run_id'),
            'status': row.status, 'workflow_execution_id': row.workflow_execution_id,
            'intent': 'question', 'outcome': row.status if row.status in {'unknown', 'failed', 'cancelled'} else 'processing', 'answer': None, 'clarify': None, 'pending_context': {}, **resolution}


async def message_history(db, user, text: str, continuation_run_id: str | None,
                          matter_id: str | None = None) -> list[dict]:
    """Retrieve attributed messages, prior choice questions and business references without reviving old memory results."""
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
        historical_matter = (context.get('api_flow') or {}).get('matter_id')
        if matter_id and historical_matter and historical_matter != matter_id:
            if row.local_run_id == continuation_run_id:
                raise ConflictError('continuation_matter_mismatch', '原消息属于其他事项，不能用于当前安排。')
            continue
        message = context.get('text', '')
        if row.local_run_id != continuation_run_id and not (terms & search_terms(message)):
            continue
        saved = response_for(row) if context.get('caller_processed') else context.get('unified_intake_result') or {}
        # This is a historical message reference, not a fresh claim about current
        # database state. Old execution references help preserve object identity.
        selected.append({'message': message[:1200], 'answer': (saved.get('answer') or saved.get('clarify') or '')[:1600],
                         'messages': [{'origin': 'user', 'text': message[:1200]},
                                      {'origin': 'assistant', 'text': (saved.get('answer') or saved.get('clarify') or '')[:1600]}],
                         'source_run_id': row.local_run_id,
                         'clarification': {'question':saved.get('clarify'),
                             'choices':(saved.get('pending_context') or {}).get('choices',[])} if saved.get('clarify') else None,
                         'references': [receipt for receipt in (context.get('api_flow') or {}).get('receipts', [])
                            if receipt.get('operation_id') not in {'recall_business_memory','save_business_memory_fact','mutate_business_memory'}][-2:],
                         'matter_id': saved.get('matter_id'), 'draft_id': saved.get('draft_id')})
        if len(selected) >= 5:
            break
    return list(reversed(selected))


def model_context(flow: dict, catalog: OperationCatalog, *, catalog_query: str = '', catalog_offset: int = 0,
                  initial_message: bool = True) -> dict:
    """Put an index directly in each new Agent's first message, without forcing a tool request.

    A new message cannot borrow definition flags from another Agent. Explicit
    searches within the same Agent retain its already supplied definitions.
    """
    from assistant_api.services.api_operations import DEFINITION_RESOLVER
    index = catalog.index_page(query=catalog_query, offset=catalog_offset)
    selected = [catalog.operations[DEFINITION_RESOLVER]]
    if initial_message:
        flow['offered_operations'] = []
        flow['provided_schema_names'] = []
    previous = set(flow.get('offered_operations', []))
    new_definitions = [operation for operation in selected if operation.operation_id not in previous]
    flow['catalog_mode'] = 'indexed'
    flow['offered_operations'] = list(dict.fromkeys([*flow.get('offered_operations', []), DEFINITION_RESOLVER]))
    shared_schemas = {name: schema for operation in new_definitions for name, schema in operation.components.items()
                      if name not in flow.get('provided_schema_names', [])}
    flow['provided_schema_names'] = list(dict.fromkeys([*flow.get('provided_schema_names', []), *shared_schemas]))
    decision_schema = OperationDecision.model_json_schema()
    if flow.get('presentation_only'):
        decision_schema['properties']['decision']['enum'] = ['answer']
    return {'text': flow['text'], 'now': flow['now'], 'timezone': flow['timezone'], 'trigger_source': flow.get('trigger_source', 'chat'),
            'agent_budget': flow.get('agent_budget', {}),
            'actor': flow.get('actor'),
            'current_message': {'origin': 'system' if flow.get('trigger_source') == 'follow_up' else 'user', 'text': flow['text']},
            'supplement_context': flow.get('supplement_context', []),
            'follow_up_id': flow.get('follow_up_id'), 'follow_up_execution_id': flow.get('follow_up_execution_id'),
            'history': flow['history'], 'operation_index': index,
            'api_operations': [op.model_description(include_components=False) for op in new_definitions],
            'components': {'schemas': shared_schemas}, 'decision_schema': decision_schema, 'presentation_only': flow.get('presentation_only', False),
            'presentation_feedback': flow.get('presentation_feedback'),
            'execution_results': [dict(receipt, step=receipt.get('step', i)) for i, receipt in enumerate(flow['receipts'])], 'card_schema': response_cards.presentation_schema(),
            'idempotency_key': f"api-call-{flow.setdefault('operation_request_id', str(uuid4()))}-{flow['step']}", 'api_internal': flow['step'] > 0,

            'argument_feedback': flow.get('argument_feedback'),
            'catalog_feedback': '索引已经随消息提供，无需先search。普通问答可直接answer，不清楚可直接ask_user；'
                '只有需要尚未加载完整定义的业务接口时才按uri解析，可同时取多个。当前Agent已有定义直接用，参数错误不重新解析；'
                '只有operation_definition_stale才需要重取已失效定义，其他未变化定义继续有效。'}


async def intake(request: Request, db, user, *, text: str, idempotency_key: str,
                 continuation_run_id: str | None = None, matter_id: str | None = None,
                 memory_disclosures: list | None = None) -> dict:
    """Claim the local request once and start one Nexus Agent for the saved task."""
    if not isinstance(request, ExecutionContext):
        request = ExecutionContext.from_request(request, user)
    if memory_disclosures:
        raise AssistantError('api_context_scope', '本次接口调用只使用当前输入与相关历史消息，请取消额外记忆选择。', 422)
    key = 'matter-intake:' + idempotency_key
    existing = await db.scalar(select(NexusRun).where(NexusRun.user_id == user.user_id, NexusRun.idempotency_key == key))
    if existing and matter_id and (existing.context_json or {}).get('api_flow', {}).get('matter_id') != matter_id:
        raise ConflictError('idempotency_conflict', '该请求标识已用于其他事项。')
    if existing is not None:
        if (existing.context_json or {}).get('text') != text:
            raise ConflictError('idempotency_conflict', '同一请求标识已用于其他输入。')
        if (existing.context_json or {}).get('caller_processed'):
            # Re-read only. Unknown starts never create a second Run or new key.
            if existing.nexus_run_id:
                await run_service.refresh_run(db, request.app.state.nexus_client, user=user, row=existing)
            return response_for(existing)
        from assistant_api.services import matters
        return await matters.get_intake_result(db, user, existing.local_run_id)
    if matter_id:
        from assistant_api.services.matters import _get_matter
        await _get_matter(db,user,matter_id)
    history = await message_history(db, user, text, continuation_run_id, matter_id)
    from assistant_api.services import supplements
    supplement_context = await supplements.context_records(db, user.user_id, matter_id) if matter_id else []
    if matter_id:
        # A route selected by the user is an explicit local reference, not bulk
        # content from the matter or its schedules.
        history.append({'selected_matter_id': matter_id})
    from assistant_api.services.matters import user_timezone
    timezone = await user_timezone(db, user, request.app.state.settings.matter_default_timezone)
    flow = {'text': text, 'query': text, 'now': datetime.now(UTC).isoformat(), 'timezone': timezone,
            'actor': {'origin': 'system' if request.source == 'follow_up' else 'user', 'user_id': user.user_id},
            'supplement_context': supplement_context,
            'history': history, 'trigger_source': request.source, 'matter_id': matter_id, 'follow_up_id': request.follow_up_id, 'follow_up_execution_id': request.follow_up_execution_id, 'lease_owner': request.lease_owner, 'receipts': [], 'step': 0, 'state': 'planning', 'request_key': idempotency_key}
    catalog = OperationCatalog(request.app)
    flow['agent_budget'] = {'max_model_calls': request.app.state.settings.nexus_business_max_model_calls,
                           'wall_time_limit_ms': request.app.state.settings.nexus_business_wall_time_limit_ms}
    context = model_context(flow, catalog)
    context.update(caller_processed=True, api_flow=flow)
    context['caller_workflow'] = workflow_configuration(context)
    if continuation_run_id:
        context['parent_run_id'] = continuation_run_id
    row, replayed = await run_service.create_ai_task(
        db, user=user, purpose=matter_plan.PURPOSE,
        trigger=ai_tasks.TRIGGER_SCHEDULE if request.source == 'follow_up' else ai_tasks.TRIGGER_CHAT,
        idempotency_key=key, context=context,
        attach=lambda record: setattr(record, 'context_json', copy.deepcopy(context)),
        strict_input_match=False,
    )
    if replayed:
        if matter_id and (row.context_json or {}).get('api_flow',{}).get('matter_id') != matter_id:
            raise ConflictError('idempotency_conflict','该请求标识已用于其他事项。')
        if (row.context_json or {}).get('text') != text:
            raise ConflictError('idempotency_conflict','同一请求标识已用于其他输入。')
        return response_for(row)
    row = await run_service.launch_ai_task(db, request.app.state.nexus_client,
        request.app.state.settings, request.app.state.session_factory, user=user, row=row)
    row, _ = await run_service.wait_terminal(db, request.app.state.nexus_client,
        user=user, row=row, timeout_seconds=request.app.state.settings.matter_ai_timeout_seconds)
    result = response_for(row)
    if continuation_run_id:
        from assistant_api.services.follow_ups import continue_execution
        await continue_execution(db, user.user_id, continuation_run_id, result)
    return result


def workflow_configuration(context: dict) -> dict:
    """Freeze per-request protocol, finite call budget and terminal schema before launch.

    The user request carries a compact index and only the resolver's full schema.
    The Agent requests business definitions by URI. The protocol reports
    uncertain writes without choosing the next action.
    It never loads every application operation or lets the model select credentials.
    """
    definitions = [{k: op[k] for k in ('operation_id', 'method', 'path', 'summary') if k in op}
                   for op in context['api_operations']]
    instructions = matter_plan.API_PROMPT.format(payload=json.dumps(
        {'bootstrap_operations': definitions,
         'schema_location': 'user_request.operation_index是一级简要目录，api_operations及components.schemas是已加载的完整定义。'},
        ensure_ascii=False))
    instructions += ('\n原始完整JSON正文交由Assistant同步处理；读取返回的实际结果后在同一个Agent继续。'
                     '先阅读operation_index中的简要说明、uri和effect，自行判断需要哪些接口；方法和路径在完整定义中。'
                     '索引已经直接包含在本Agent首轮消息中，看到索引不增加请求，不强制发起search或解析。'
                     '普通问答直接answer，需求不清楚先ask_user；不为问候、回答或追问而获取业务定义。'
                     '首次只有resolve_operation_definitions已加载完整定义；仅当你决定使用尚未加载的业务接口时，'
                     '用decision=call、operation_id=resolve_operation_definitions、body={"uris":["从索引选择的完整uri"]}'
                     '取得完整参数及共享结构，可一次获取本任务需要的多个接口，再自行决定是否调用。'
                     '读取目录和解析定义都不执行业务，也不是要求用户确认；不要因为多了目录就反复问用户是否执行明确请求。'
                     '需要更多目录时decision=search，search_query由你给出关键词；空字符串及search_offset可分页读取全部目录。'
                     '搜索只返回简要目录，不自动加载完整定义。已经加载的定义和共享结构留在本Agent，不重复获取。'
                     '已加载必须是本Agent初始消息或本运行成功返回的工具结果中确实提供过的完整定义；服务器缓存或上一个Agent看过不算。'
                     '同Agent再次使用相同接口直接调用，发现新能力只取新增定义。只有参数错误而定义未变化时按反馈修正，不重新解析。'
                     '服务端返回operation_definition_stale表示对应定义已变化且本条尚未执行，才重新取得该定义；新返回说明替代同名旧说明。'
                     '历史用户文字和AI回答不是本次要求，只有后端执行结果证明保存成功。'
                     '创建待办不代表完成待办；保存提醒不代表通知送达。'
                     '业务成功后自主判断回答或必要下一步，不重复成功操作。'
                     '参数校验失败可修正；拒绝或结果未知不得重新提交。'
                     '收到invalid_parameters时按字段路径和格式要求仅修正当前输出，不重做先前成功操作。'
                     'HTTP409本身不证明未写入；unknown/stop、超时或响应丢失不得换编号重试写入。'
                     'HTTP成功不代表路线可用，读取data.status、reason_code及实际duration_seconds。'
                     '先规划必要查询和测算，避免重复核对已经唯一确定的地点；调用预算包含最终回答，需预留终答。'
                     '道路走法由地图供应商计算，你负责选择地点顺序、逐段方式和时间衔接，不虚构耗时。'
                     '所有输出只返回一个完整JSON对象，首字符必须为{，末字符必须为}；不要Markdown、```json或其他代码围栏，尤其最终回答必须原始JSON。')
    if len(instructions) > 16000:
        raise AssistantError('caller_protocol_too_large', '本次接口说明超出平台限制。', 422)
    return {'application_response_mode': 'caller_processed',
            'max_model_calls': context.get('agent_budget', {}).get('max_model_calls', 24),
            'wall_time_limit_ms': context.get('agent_budget', {}).get('wall_time_limit_ms', 600000),
            'application_native_tools_enabled': False,
            'application_protocol_instructions': instructions,
            'final_output_schema': {'type': 'object', 'required': ['decision'],
                'properties': {'decision': {'enum': ['answer', 'ask_user', 'clarify']},
                               'answer': {'type': 'string'}, 'question': {'type': 'string'},
                               'choices': {'type': 'array', 'items': {'type': 'string'}},
                               'cards': {'type': 'array', 'items': {'type': 'object'}},
                               'notify': {'type': 'boolean'}}, 'additionalProperties': False}}
