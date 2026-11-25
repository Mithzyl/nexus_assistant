"""Expose business descriptions without interpreting the user's positive or negative intent."""
from assistant_api.services.api_operations import OperationCatalog


async def test_daily_route_task_offers_requested_business_capabilities(harness):
    """A multi-capability task has a wide index; selecting or forbidding actions belongs to the Agent."""
    query=('请新增两项今天的待办：到博物馆取资料、到剧院交材料。'
           '请查询已有通勤计划、今天的日程与已有待办，搜索地点并实际测算路线。'
           '不修改原通勤和原日程，不重复创建已有待办。'
           '路线只给建议，不另建日程，不把建议说成已经执行。')
    offered=OperationCatalog(harness.app).index_page()['items']
    uris={op['uri'] for op in offered}
    catalog=OperationCatalog(harness.app)
    identities={(op.method,op.path) for op in catalog.operations.values() if op.uri in uris}
    assert {('POST','/api/v1/todos'),('GET','/api/v1/todos'),
            ('GET','/api/v1/commute-plans'),('GET','/api/v1/today'),
            ('POST','/api/v1/places/search'),('POST','/api/v1/commute/estimate')} <= identities
    assert len(offered)>8
    assert ('POST','/api/v1/schedules') in identities


async def test_primary_matches_keep_space_before_unrequested_continuations(harness):
    """Immediate draft links must not evict other primary matches in the same bounded catalogue."""
    catalog=OperationCatalog(harness.app)
    linked=next(op for op in catalog.operations.values() if len(op.definition.get('next_operations',[]))>=2)
    chosen=[linked]+[op for op in catalog.operations.values() if op.operation_id!=linked.operation_id][:7]
    offered=catalog.with_next_operations(chosen)
    assert {op.operation_id for op in chosen} <= {op.operation_id for op in offered}
    assert len(offered)<=8


async def test_explicit_edit_keeps_its_published_next_steps(harness):
    """Real edit intent must retain the edit operation and its next-step definitions."""
    catalog=OperationCatalog(harness.app)
    offered=catalog.search('修改已有日程的名称')
    assert any(op.method in {'PATCH','PUT'} or 'edit' in op.operation_id for op in offered)
    edit=next(op for op in catalog.operations.values() if 'edit_draft' in op.operation_id)
    continued=catalog.with_next_operations([],previous=edit.operation_id)
    assert {x['operation_id'] for x in edit.definition['next_operations']} <= {op.operation_id for op in continued}


async def test_negative_phrases_are_not_removed_by_the_catalogue(harness):
    """Catalogue matching never removes an action because of a backend negation rule."""
    offered=OperationCatalog(harness.app).search('不要修改日程')
    assert 'update_schedule' in {operation.operation_id for operation in offered}


def test_business_agent_explicitly_uses_caller_tools_only():
    """Business workflows opt out of native tools without changing ordinary client defaults."""
    from assistant_api.nexus_client.models import WorkflowCreateRequest
    from assistant_api.services.operation_intake import workflow_configuration

    ordinary=WorkflowCreateRequest(idempotency_key='ordinary-test')
    business=WorkflowCreateRequest(idempotency_key='business-test',
        **workflow_configuration({'api_operations': []}))
    assert ordinary.application_native_tools_enabled is True
    assert ordinary.application_response_mode == 'standard'
    assert business.application_native_tools_enabled is False
    assert business.application_response_mode == 'caller_processed'
