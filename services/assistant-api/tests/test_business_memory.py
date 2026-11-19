"""Focused memory integration checks using real Assistant routes and isolated SQLite.

Only Nexus HTTP responses are fixed; no production data, model, map or hash
checks are used. These checks do not claim live Nexus write acceptance.
"""
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from assistant_api.models import MatterTodo, NexusRun
from assistant_api.services.api_operations import OperationCatalog
from assistant_api.services.operation_intake import model_context
from conftest import auth, tool_auth


@asynccontextmanager
async def upstream_client(h, handler):
    """Replace only this isolated app's Nexus transport and restore its pool."""
    nexus = h.app.state.nexus_client
    original = nexus._client
    nexus._client = httpx.AsyncClient(base_url='http://nexus.test', transport=httpx.MockTransport(handler))
    try:
        yield
    finally:
        await nexus._client.aclose()
        nexus._client = original


async def prepare_business_run(h, text):
    """Create an owned, active body-processing run without calling any model."""
    flow = {'text': text, 'query': '业务记忆 查询记忆 保存记忆 创建待办',
            'now': '2030-01-01T09:00:00Z', 'timezone': 'Asia/Shanghai',
            'history': [], 'receipts': [], 'step': 0, 'trigger_source': 'chat'}
    catalog = OperationCatalog(h.app)
    context = model_context(flow, catalog)
    flow['offered_operations'] = list(catalog.operations)
    context.update(caller_processed=True, api_flow=flow)
    row = NexusRun(user_id=h.user_a, nexus_run_id=str(uuid4()), workflow_execution_id=str(uuid4()),
                   idempotency_key=str(uuid4()), request_text=text, purpose='matter_plan',
                   status='running', context_json=context)
    async with h.app.state.session_factory() as db:
        db.add(row)
        await db.commit()
    return row


async def body_call(h, row, operation, body, execution):
    """Send a fixed model proposal through the actual authenticated callback."""
    proposal = {'decision': 'call', 'call': {'operation_id': operation, 'body': body,
                                           'summary': '使用当前用户业务记忆'}}
    return await h.client.post('/internal/nexus/agent-responses', headers=tool_auth(h.settings), json={
        'protocol_version': '1', 'application_id': 'personal_assistant',
        'external_user_id': h.user_a, 'run_id': row.nexus_run_id,
        'workflow_id': row.workflow_execution_id, 'response_id': execution,
        'execution_id': execution, 'content': json.dumps(proposal, ensure_ascii=False)})


async def test_management_has_no_run_dependency_and_preserves_typed_history_undo(harness):
    """New accounts manage typed memory and history using only App management routes."""
    h = harness
    calls = []
    entry = str(uuid4())

    async def handler(request):
        """Expose the current App contract, retaining the authenticated account header."""
        calls.append(request)
        assert request.headers['x-external-user-id'] == h.user_a
        assert request.url.path == '/api/v1/app/me/user-memories'
        if request.method == 'POST':
            value = json.loads(request.content)
            assert 'local_run_id' not in value and value['scope'] == 'personal'
            if value['action'] == 'save':
                assert value['typed_data']['memory_type'] == 'place'
                assert value['confirmed_type'] is True
            return httpx.Response(200, json={'entry_id': entry, 'status': 'active', 'version': 2, 'applied_version': 2})
        if request.url.params.get('history_entry_id'):
            assert request.url.params['history_entry_id'] == entry
            return httpx.Response(200, json={'current_version': 2, 'items': [{'version': 1, 'text': '公司旧地址'}], 'next_offset': None})
        return httpx.Response(200, json={'items': [], 'next_offset': None})

    async with upstream_client(h, handler):
        assert (await h.client.get('/api/v1/memory', headers=auth(h.token_a))).status_code == 200
        saved = await h.client.post('/api/v1/memory/commands', headers=auth(h.token_a), json={
            'action': 'save', 'key': 'business-place-save', 'text': '公司在人民广场',
            'confirmed_sensitive': True, 'confirmed_type': True,
            'typed_data': {'memory_type': 'place', 'label': '公司', 'address': '人民广场', 'coordinate_system': 'GCJ02'}})
        assert saved.status_code == 200, saved.text
        history = await h.client.get('/api/v1/memory/history/'+entry, headers=auth(h.token_a))
        assert history.json()['current_version'] == 2
        undo = await h.client.post('/api/v1/memory/commands', headers=auth(h.token_a), json={
            'action': 'undo', 'key': 'business-place-undo', 'entry_id': entry, 'expected_version': 2})
        assert undo.status_code == 200
        assert len(calls) == 4


@pytest.mark.parametrize('status,code', [(409, 'memory_version_conflict'), (503, 'memory_storage_unavailable')])
async def test_memory_business_errors_keep_their_status_and_code(harness, status, code):
    """Known memory failures remain distinguishable without leaking upstream details."""
    async def handler(request):
        """Return a documented Nexus memory failure."""
        return httpx.Response(status, json={'detail': {'code': code}})
    async with upstream_client(harness, handler):
        response = await harness.client.get('/api/v1/memory', headers=auth(harness.token_a))
        assert response.status_code == status
        assert response.json()['error']['code'] == code


async def test_recall_filters_active_business_memory_across_pages(harness):
    """Business recall retains structured values and provenance, excluding candidates."""
    calls = []
    async def handler(request):
        """Return two real-shaped pages with a candidate before useful facts."""
        calls.append(request.url.path)
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True, 'version': 0})
        offset = int(request.url.params.get('offset', 0))
        if offset == 0:
            values = [{'entry_id': 'candidate', 'status': 'candidate', 'version': 1, 'text': '公司待核实', 'sensitive': True},
                      {'entry_id': 'place', 'status': 'active', 'version': 2, 'text': '公司', 'sensitive': True,
                       'typed_data': {'memory_type': 'place', 'label': '公司', 'address': '人民广场', 'coordinate_system': 'GCJ02'}}]
            return httpx.Response(200, json={'items': values, 'next_offset': 2})
        return httpx.Response(200, json={'items': [{'entry_id': 'commute', 'status': 'active', 'version': 3,
            'text': '平时步行上班', 'sensitive': True, 'typed_data': {'memory_type': 'commute_preference',
                'origin': '家', 'destination': '公司', 'travel_mode': 'walk', 'timezone': 'Asia/Shanghai', 'weekdays': [1]}},
            {'entry_id': 'other-matter', 'status': 'active', 'version': 1, 'text': '另一事项地点',
             'app_data': {'fields': {'business_category': 'place', 'applicability': {'matter_id': str(uuid4())}}}}], 'next_offset': None})
    async with upstream_client(harness, handler):
        response = await harness.client.post('/api/v1/business-memory/query', headers=auth(harness.token_a),
            json={'purpose': 'route', 'limit': 5})
        assert response.status_code == 200, response.text
        value = response.json()
        assert [i['entry_id'] for i in value['items']] == ['place', 'commute']
        assert value['items'][1]['business']['transport_mode'] == 'walking'
        assert value['items'][0]['typed_data']['coordinate_system'] == 'GCJ02'
        assert value['items'][0]['source']['version'] == 2
        assert len(calls) == 3 and value['next_offset'] is None


async def test_disabled_memory_stops_business_reads_not_owner_management(harness):
    """A disabled platform setting prevents recall before any memory body is read."""
    calls = []
    async def handler(request):
        """Keep explicit management available while automatic use is disabled."""
        calls.append(request.url.path)
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': False, 'version': 1})
        return httpx.Response(200, json={'items': [], 'next_offset': None})
    async with upstream_client(harness, handler):
        response = await harness.client.post('/api/v1/business-memory/query', headers=auth(harness.token_a), json={'purpose': 'route'})
        assert response.json()['status'] == 'disabled' and response.json()['items'] == []
        assert len(calls) == 1
        assert (await harness.client.get('/api/v1/memory', headers=auth(harness.token_a))).status_code == 200


async def test_one_agent_saves_quoted_fact_recalls_and_creates_todo(harness):
    """Actual callback/record routes preserve memory provenance through one business run."""
    h = harness
    calls = []
    entry = str(uuid4())
    stored = False
    async def handler(request):
        """Acknowledge the exact user fact and return its source on recall."""
        nonlocal stored
        calls.append(request)
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True, 'version': 0})
        if request.method == 'POST':
            value = json.loads(request.content)
            assert value['text'] == '上班记得带工牌' and value['action'] == 'save'
            assert value['app_data']['fields']['source']['local_run_id'] == row.local_run_id
            stored = True
            return httpx.Response(200, json={'entry_id': entry, 'version': 1, 'status': 'active'})
        if not stored:
            return httpx.Response(200, json={'items': [], 'next_offset': None})
        if request.url.params.get('history_entry_id'):
            return httpx.Response(200, json={'entry_id': entry, 'current_version': 1,
                'items': [{'version': 1, 'status': 'active', 'text': '上班记得带工牌'}], 'next_offset': None})
        return httpx.Response(200, json={'items': [{'entry_id': entry, 'version': 1, 'status': 'active',
            'sensitive': True, 'text': '上班记得带工牌', 'app_data': {'namespace': 'personal_assistant',
                'schema_version': 1, 'fields': {'business_category': 'procedure'}}}], 'next_offset': None})
    row = await prepare_business_run(h, '请记住：上班记得带工牌。查询记忆并创建对应待办。')
    async with upstream_client(h, handler):
        saved = await body_call(h, row, 'save_business_memory_fact',
            {'source_quote': '上班记得带工牌', 'category': 'procedure'}, 'memory-write-one')
        assert saved.json()['execution_status'] == 'succeeded', saved.text
        assert (await body_call(h, row, 'save_business_memory_fact',
            {'source_quote': '上班记得带工牌', 'category': 'procedure'}, 'memory-write-one')).json() == saved.json()
        recalled = await body_call(h, row, 'recall_business_memory', {'purpose': 'general', 'query': '工牌'}, 'memory-read-one')
        assert recalled.json()['execution_status'] == 'succeeded', recalled.text
        assert recalled.json()['result']['data']['items'][0]['entry_id'] == entry
        created = await body_call(h, row, 'create_todo', {'title': '上班记得带工牌', 'idempotency_key': 'model-todo-memory'}, 'memory-todo-one')
        assert created.json()['execution_status'] == 'succeeded', created.text
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(NexusRun)) == 1
        todos = list(await db.scalars(select(MatterTodo)))
        assert len(todos) == 1 and todos[0].title == '上班记得带工牌'
        saved_row = await db.get(NexusRun, row.local_run_id)
        results = saved_row.context_json['api_flow']['results']
        assert results['1']['data']['items'][0]['source']['entry_id'] == entry
        assert sum(r.method == 'POST' for r in calls) == 1
    second = await prepare_business_run(h, '请记住：上班记得带工牌。')
    async with upstream_client(h, handler):
        reused = await body_call(h, second, 'save_business_memory_fact',
            {'source_quote': '上班记得带工牌', 'category': 'procedure'}, 'memory-second-run')
        assert reused.json()['result']['data']['reused'] is True
        assert sum(r.method == 'POST' for r in calls) == 1


@pytest.mark.parametrize('change', ['disabled', 'version'])
async def test_recalled_memory_change_prevents_dependent_write(harness, change):
    """A changed or disabled fact cannot authorize a stale subsequent todo operation."""
    h = harness
    settings_reads = 0
    async def handler(request):
        """Change the platform state between recall and the actual dependent write."""
        nonlocal settings_reads
        if request.url.path.endswith('memory-settings'):
            settings_reads += 1
            return httpx.Response(200, json={'enabled': not (change == 'disabled' and settings_reads > 1), 'version': 1})
        if request.url.params.get('history_entry_id'):
            return httpx.Response(200, json={'entry_id': 'fact', 'current_version': 2,
                'items': [{'version': 2, 'status': 'active', 'text': '上班带工牌'}], 'next_offset': None})
        return httpx.Response(200, json={'items': [{'entry_id': 'fact', 'version': 2 if settings_reads > 1 else 1,
            'status': 'active', 'sensitive': False, 'text': '上班带工牌'}], 'next_offset': None})
    row = await prepare_business_run(h, '读取记忆里的工牌安排并添加待办。')
    async with upstream_client(h, handler):
        recalled = await body_call(h, row, 'recall_business_memory', {'purpose': 'general', 'query': '工牌'}, 'recall-change')
        assert recalled.json()['execution_status'] == 'succeeded'
        result = await body_call(h, row, 'create_todo', {'title': '带工牌', 'idempotency_key': 'stale-todo'}, 'write-change')
        assert result.json()['execution_status'] == 'rejected', result.text
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo)) == 0


async def test_models_cannot_spoof_memory_source_or_access_management_tools(harness):
    """Only a server-bound current user quote permits the restricted write operation."""
    h = harness
    catalog = OperationCatalog(h.app)
    assert {'recall_business_memory', 'save_business_memory_fact'} <= set(catalog.operations)
    assert all(not o.path.startswith('/api/v1/memory') for o in catalog.operations.values())
    response = await h.client.post('/api/v1/business-memory/facts', headers={**auth(h.token_a),
        'X-Assistant-Execution-Run-Id': str(uuid4())}, json={'source_quote': '伪造内容', 'category': 'procedure'})
    assert response.status_code == 403
    row = await prepare_business_run(h, '请不要记住这次的出行。')
    denied = await body_call(h, row, 'save_business_memory_fact', {'source_quote': '这次的出行', 'category': 'travel_policy'}, 'invalid-memory-source')
    assert denied.json()['execution_status'] != 'succeeded'
    assert not any(c['path'].endswith('/me/user-memories') for c in h.fake.calls)


@pytest.mark.parametrize('metadata', [{}, {'category': '陪伴家人', 'tags': ['少走路', '陪同', '少走路']}])
async def test_open_memory_save_does_not_require_a_known_category(harness, metadata):
    """Unknown domains and unclassified quotes remain writable without rewriting user text."""
    h = harness
    quote = '带母亲出门时尽量少走路，她不喜欢坐地铁'
    row = await prepare_business_run(h, '请记住：'+quote+'。')
    writes = []

    async def handler(request):
        """Accept only the exact quote and optional open labels in the existing App envelope."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        if request.method == 'POST':
            value = json.loads(request.content)
            writes.append(value)
            assert value['text'] == quote
            fields = value['app_data']['fields']
            if metadata:
                assert fields['business_category'] == '陪伴家人'
                assert fields['tags'] == ['少走路', '陪同']
            else:
                assert 'business_category' not in fields and 'tags' not in fields
            return httpx.Response(200, json={'entry_id': str(uuid4()), 'version': 1, 'status': 'active'})
        return httpx.Response(200, json={'items': [], 'next_offset': None})

    async with upstream_client(h, handler):
        result = await body_call(h, row, 'save_business_memory_fact', {'source_quote': quote, **metadata}, 'open-save')
        assert result.json()['execution_status'] == 'succeeded', result.text
        assert len(writes) == 1
    schemas = h.app.openapi()['components']['schemas']
    assert 'category' not in schemas['BusinessFactRequest'].get('required', [])
    assert 'enum' not in schemas['BusinessRecallRequest']['properties']['purpose']


async def test_recall_never_excludes_domains_by_category_or_missing_labels(harness):
    """A route task can retrieve family, health and unknown labels; hints only change order."""
    async def handler(request):
        """Return valid current facts whose classifications used to exclude them from route queries."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        return httpx.Response(200, json={'items': [
            {'entry_id': 'unlabelled', 'version': 1, 'status': 'active', 'text': '母亲不喜欢坐地铁'},
            {'entry_id': 'new-domain', 'version': 1, 'status': 'active', 'text': '陪同家人时少走路',
             'app_data': {'fields': {'business_category': '家庭照护', 'tags': ['陪同']}}},
            {'entry_id': 'legacy', 'version': 1, 'status': 'active', 'text': '去医院需要先拿材料',
             'app_data': {'fields': {'business_category': 'procedure'}}}], 'next_offset': None})

    async with upstream_client(harness, handler):
        response = await harness.client.post('/api/v1/business-memory/query', headers=auth(harness.token_a),
            json={'purpose': '陪母亲去医院的路线', 'categories': ['从未预设的分类'], 'tags': ['陪同']})
        assert response.status_code == 200, response.text
        data = response.json()
        assert {item['entry_id'] for item in data['items']} == {'unlabelled', 'new-domain', 'legacy'}
        assert data['items'][0]['business']['category'] == '家庭照护'
        assert data['coverage']['state'] == 'complete'
        assert data['retrieval_method'] == 'browse'


async def test_open_retrieval_supports_alternative_phrases_and_preserves_full_text(harness):
    """Multiple expressions and partial lexical matches do not truncate long source text."""
    original = '母亲不喜欢坐地铁。' + '需要保留的原始说明。' * 260
    async def handler(request):
        """Provide free text without imposing domain-specific synonym rules."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        return httpx.Response(200, json={'items': [{'entry_id': 'family', 'version': 1, 'status': 'active',
            'text': original}], 'next_offset': None})

    async with upstream_client(harness, handler):
        response = await harness.client.post('/api/v1/business-memory/query', headers=auth(harness.token_a),
            json={'query': '陪同家人的公共交通偏好', 'queries': ['地铁', '陪母亲出行']})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['items'][0]['text'] == original
        assert '地铁' in data['items'][0]['match']['matched_queries']
        missing = await harness.client.post('/api/v1/business-memory/query', headers=auth(harness.token_a),
            json={'query': '火星采矿'})
        assert missing.json()['match_status'] == 'no_match'
        assert 'browse' in missing.json()['next_steps']
        assert missing.json()['coverage']['state'] == 'complete'


async def test_scan_continuation_and_version_validation_reach_entries_after_200(harness):
    """Later pages remain discoverable and version checking does not restart a limited scan."""
    h = harness
    target = str(uuid4())
    entries = [{'entry_id': str(uuid4()), 'version': 1, 'status': 'active', 'text': '无关资料'} for _ in range(250)]
    entries.append({'entry_id': target, 'version': 4, 'status': 'active', 'text': '母亲就医需要携带材料'})
    async def handler(request):
        """Serve bounded source pages and the existing owned current-version history lookup."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        if request.url.params.get('history_entry_id'):
            assert request.url.params['history_entry_id'] == target
            return httpx.Response(200, json={'entry_id': target, 'current_version': 4,
                'items': [{'version': 4, 'status': 'active', 'text': entries[-1]['text']}], 'next_offset': None})
        offset, limit = int(request.url.params['offset']), int(request.url.params['limit'])
        return httpx.Response(200, json={'items': entries[offset:offset+limit],
            'next_offset': offset+limit if offset+limit < len(entries) else None})

    async with upstream_client(h, handler):
        first = await h.client.post('/api/v1/business-memory/query', headers=auth(h.token_a), json={'query': '就医'})
        assert first.json()['items'] == []
        assert first.json()['next_offset'] == 200 and first.json()['coverage']['state'] == 'partial'
        row = await prepare_business_run(h, '查询就医记忆并创建待办')
        found = await body_call(h, row, 'recall_business_memory', {'query': '就医', 'offset': 200}, 'later-recall')
        assert found.json()['result']['data']['items'][0]['entry_id'] == target
        created = await body_call(h, row, 'create_todo', {'title': '携带就医材料', 'idempotency_key': 'late-fact'}, 'later-write')
        assert created.json()['execution_status'] == 'succeeded', created.text


async def test_scan_limit_does_not_refuse_new_unclassified_memory(harness):
    """An incomplete optional duplicate lookup must not reject a new, explicitly remembered fact."""
    h = harness
    row = await prepare_business_run(h, '请记住：我的望远镜需要先校准。')
    async def handler(request):
        """Expose more than 200 unrelated entries, then accept one idempotent save."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        if request.method == 'POST':
            assert json.loads(request.content)['text'] == '我的望远镜需要先校准'
            return httpx.Response(200, json={'entry_id': str(uuid4()), 'version': 1, 'status': 'active'})
        offset = int(request.url.params['offset'])
        return httpx.Response(200, json={'items': [{'entry_id': str(uuid4()), 'version': 1, 'status': 'active',
            'text': '其他记忆'} for _ in range(50)], 'next_offset': offset+50})
    async with upstream_client(h, handler):
        result = await body_call(h, row, 'save_business_memory_fact', {'source_quote': '我的望远镜需要先校准'}, 'large-save')
        assert result.json()['execution_status'] == 'succeeded', result.text
        assert result.json()['result']['data']['duplicate_check_complete'] is False


async def test_management_search_accepts_open_label_hints_without_excluding_other_memories(harness):
    """Legacy type hints cannot hide a matching quote that has no classification."""
    async def handler(request):
        """Serve an owned unlabelled note containing the requested text."""
        return httpx.Response(200, json={'items': [{'entry_id': 'telescope', 'version': 1, 'status': 'active',
            'text': '望远镜每次使用前需要校准'}], 'next_offset': None})
    async with upstream_client(harness, handler):
        response = await harness.client.post('/api/v1/memory/search', headers=auth(harness.token_a),
            json={'query_text': '望远镜使用要求', 'memory_types': ['天文设备'], 'idempotency_key': 'open-label-search'})
        assert response.status_code == 200, response.text
        assert response.json()['items'][0]['memory_id'] == 'telescope'


async def test_full_memory_results_resume_before_response_size_limit(harness):
    """Large originals are paged at entry boundaries instead of being truncated or lost."""
    h = harness
    entries = [{'entry_id': str(uuid4()), 'version': 1, 'status': 'active', 'text': '完整原文'*1000,
        'app_data': {'fields': {'business_value': {'explanation': '附加说明'*600}}}} for _ in range(10)]
    async def handler(request):
        """Return valid maximum-length text and bounded extension values on each source row."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        offset = int(request.url.params['offset'])
        return httpx.Response(200, json={'items': entries[offset:], 'next_offset': None})
    async with upstream_client(h, handler):
        first = await h.client.post('/api/v1/business-memory/query', headers=auth(h.token_a), json={'limit': 10})
        data = first.json()
        assert data['coverage']['reason'] == 'response_size'
        assert len(first.content) < 120000
        assert data['next_offset'] == len(data['items'])
        second = await h.client.post('/api/v1/business-memory/query', headers=auth(h.token_a),
            json={'offset': data['next_offset'], 'limit': 10})
        all_items = data['items'] + second.json()['items']
        assert {item['entry_id'] for item in all_items} == {item['entry_id'] for item in entries}
        assert all(item['text'] == entries[0]['text'] for item in all_items)


async def test_open_domains_do_not_override_explicit_scope_or_expiry(harness):
    """Open category matching preserves the user's actual use and date restrictions."""
    async def handler(request):
        """Provide one available fact plus explicitly restricted and expired facts."""
        if request.url.path.endswith('memory-settings'):
            return httpx.Response(200, json={'enabled': True})
        return httpx.Response(200, json={'items': [
            {'entry_id': 'available', 'version': 1, 'status': 'active', 'text': '任意领域原话'},
            {'entry_id': 'restricted', 'version': 1, 'status': 'active', 'text': '仅用于日程的内容',
             'app_data': {'fields': {'business_category': '新领域', 'applicability': {'purposes': ['schedule']}}}},
            {'entry_id': 'expired', 'version': 1, 'status': 'active', 'text': '已经过期的内容',
             'app_data': {'fields': {'applicability': {'effective_until': '2000-01-01'}}}}], 'next_offset': None})
    async with upstream_client(harness, handler):
        response = await harness.client.post('/api/v1/business-memory/query', headers=auth(harness.token_a),
            json={'purpose': 'route'})
        assert [item['entry_id'] for item in response.json()['items']] == ['available']
