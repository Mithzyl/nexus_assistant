"""Focused contract for compact, shared OpenAPI schemas in real record planning turns."""
from assistant_api.services.api_operations import OperationCatalog
from assistant_api.services.operation_intake import model_context
from assistant_api.services.matter_plan import prepare_input


def test_selected_record_operations_share_schema_components_once(harness):
    """A multi-step append flow fits the existing Nexus input limit without truncating interface definitions."""
    flow = {'text': '给当前项目评审事项补一个核对报价的待办，不设时间，不修改原日程。',
            'query': '给当前项目评审事项补一个核对报价的待办，不设时间，不修改原日程。',
            'history': [{'selected_matter_id': 'existing-matter'}], 'now': '2026-11-03T00:00:00Z',
            'timezone': 'Asia/Shanghai', 'step': 3, 'request_key': 'compact-context-001',
            'receipts': [{'operation_id': 'confirm_draft_api_v1_matters_drafts__draft_id__confirm_post',
                          'status': 'succeeded', 'http_status': 200, 'references': {'matter_id': 'existing-matter', 'version': 2},
                          'result_shape': {'/todos': {'type': 'array', 'count': 1, 'item_fields': ['todo_id','title']}}}]}
    context = model_context(flow, OperationCatalog(harness.app))
    assert context['decision_schema']['$defs']['OperationCall']['additionalProperties'] is False
    assert 'components' in context
    assert all('components' not in operation for operation in context['api_operations'])
    assert len(prepare_input(context)) < 100000
    def check_refs(value):
        """Resolve every supplied interface reference against the one shared component dictionary."""
        if isinstance(value, dict):
            if '$ref' in value:
                assert value['$ref'].split('/')[-1] in context['components']['schemas']
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)
    check_refs({"api_operations": context["api_operations"], "components": context["components"]})


def test_commute_list_keeps_detail_and_update_reachable_after_search():
    """A narrowed list search still publishes the steps required to edit the original plan."""
    from fastapi import FastAPI
    from assistant_api.commute.router import router
    app = FastAPI()
    app.include_router(router)
    catalog = OperationCatalog(app)
    by_route = {(op.method, op.path): op for op in catalog.operations.values()}
    listed = by_route['GET', '/api/v1/commute-plans']
    detail = by_route['GET', '/api/v1/commute-plans/{plan_id}']
    update = by_route['PUT', '/api/v1/commute-plans/{plan_id}']
    following_list = catalog.with_next_operations([], previous=listed.operation_id)
    assert detail in following_list
    following_detail = catalog.with_next_operations([], previous=detail.operation_id)
    assert update in following_detail
