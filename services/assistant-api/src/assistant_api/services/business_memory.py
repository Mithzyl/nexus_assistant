"""Use Assistant-owned facts as bounded business data, never as identity or permissions."""
import json
import re
import unicodedata
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from assistant_api.core.errors import ConflictError, ForbiddenError, InvalidRequestError
from assistant_api.models import NexusRun
from assistant_api.nexus_client.errors import NexusProtocolError
from assistant_api.schemas.memory import AppMemoryData

SCAN_LIMIT = 200
# These mappings only preserve display hints for existing structured types.
# They do not enumerate the domains a memory may describe or be recalled in.
TYPE_CATEGORY = {'place': 'place', 'commute_preference': 'travel_policy',
                 'time_preference': 'routine', 'experience': 'procedure'}
UPSTREAM_MAX_OFFSET = 10000
RECALL_MAX_BYTES = 110000
MODES = {'walk': 'walking', 'bicycle': 'bicycling', 'transit': 'transit', 'drive': 'driving',
         'walking': 'walking', 'bicycling': 'bicycling', 'driving': 'driving', 'electrobike': 'electrobike'}


def memory_page(value):
    """Reject malformed upstream pages instead of manufacturing an empty account."""
    if not isinstance(value.get('items'), list):
        raise NexusProtocolError('记忆列表结构无效。')
    cursor = value.get('next_offset')
    if cursor is not None and (type(cursor) is not int or cursor < 0):
        raise NexusProtocolError('记忆分页结构无效。')
    for item in value['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('entry_id'), str)
                or type(item.get('version')) is not int or item['version'] < 1):
            raise NexusProtocolError('记忆条目结构无效。')
    return value


def business_projection(item):
    """Preserve open labels and legacy display hints without making them retrieval restrictions."""
    typed = item.get('typed_data') or {}
    app_data = item.get('app_data') or {}
    if not isinstance(app_data, dict):
        raise NexusProtocolError('记忆业务扩展结构无效。')
    fields = app_data.get('fields') or {}
    if not isinstance(typed, dict) or not isinstance(fields, dict):
        raise NexusProtocolError('记忆业务字段结构无效。')
    category = fields.get('business_category')
    if not isinstance(category, str) or not category.strip():
        kind = typed.get('memory_type')
        category = TYPE_CATEGORY.get(kind) if isinstance(kind, str) else None
        if category is None and any(k in fields for k in ('arrival_local_time', 'transport_mode', 'commute')):
            category = 'travel_policy'
    tags = fields.get('tags', [])
    tags = list(dict.fromkeys(tag for tag in tags if isinstance(tag, str) and tag.strip()))[:20] if isinstance(tags, list) else []
    result = {'category': category, 'tags': tags}
    mode = typed.get('travel_mode') or fields.get('transport_mode')
    business_value = fields.get('business_value')
    if isinstance(business_value, dict):
        mode = mode or business_value.get('transport_mode')
        result['value'] = business_value
    if isinstance(mode, str) and mode in MODES:
        result['transport_mode'] = MODES[mode]
    if isinstance(fields.get('role'), str):
        result['role'] = fields['role'][:100]
    if isinstance(fields.get('applicability'), dict):
        result['applicability'] = fields['applicability']
    elif 'applicability' in fields:
        # Invalid scope metadata must never widen an entry to all purposes.
        result['applicability'] = {'purposes': []}
    return result


def applicable(projection, purpose, timezone, matter_id=None, *, at=None):
    """Honor restrictions at the current or supplied business time; invalid scope never widens use."""
    rules = projection.get('applicability') or {}
    if rules.get('matter_id') is not None and rules['matter_id'] != matter_id:
        return False
    purposes = rules.get('purposes')
    if purposes is not None and (not isinstance(purposes, list) or purpose not in purposes):
        return False
    now = at if at is not None else datetime.now(UTC)
    for key, lower in (('effective_from', True), ('effective_until', False)):
        value = rules.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return False
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                from datetime import date
                current, bound = now.astimezone(ZoneInfo(timezone)).date(), date.fromisoformat(value)
            else:
                current, bound = now, datetime.fromisoformat(value.replace('Z', '+00:00'))
                if bound.tzinfo is None:
                    return False
            if (lower and current < bound) or (not lower and current > bound):
                return False
        except (ValueError, TypeError):
            return False
    return True


def lexical_match(item, projection, queries, hints=()):
    """Match open expressions and Unicode word/bigram fragments, with labels used only for ordering.

    This is deterministic lexical retrieval, not embeddings or semantic search.
    The original Agent can supply alternative expressions or browse original text
    when no expression matches; no domain-specific synonym dictionary is used.
    """
    text = unicodedata.normalize('NFKC', json.dumps(
        [item.get('text', ''), item.get('typed_data'), projection.get('value')], ensure_ascii=False)).casefold()
    matched, score = [], 0
    for query in dict.fromkeys(query.strip() for query in queries if query.strip()):
        normalized = unicodedata.normalize('NFKC', query).casefold()
        fragments = set()
        for word in re.findall(r'[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+', normalized):
            if re.fullmatch(r'[\u3400-\u9fff]+', word):
                fragments.update(word[index:index+2] for index in range(len(word)-1))
            elif len(word) > 1:
                fragments.add(word)
        hits = sum(fragment in text for fragment in fragments)
        exact = normalized in text
        if exact or hits:
            matched.append(query)
            score += 20 * exact + hits
    labels = [projection.get('category'), *projection.get('tags', []), (item.get('typed_data') or {}).get('memory_type')]
    matched_tags = [hint for hint in dict.fromkeys(hints) if hint in labels]
    return (score, len(matched_tags)), {'matched_queries': matched, 'matched_tags': matched_tags}


def recall_result(items, payload, scanned, cursor, reason, *, disabled=False):
    """Report the actual scanned interval and usable continuation, never infer absence from no match."""
    partial = cursor is not None or payload.offset > 0
    supported = cursor is None or cursor <= UPSTREAM_MAX_OFFSET
    searching = payload.mode != 'browse' and bool(payload.query.strip() or payload.queries)
    confirmed_empty = not disabled and payload.offset == 0 and scanned == 0 and cursor is None
    next_steps = []
    if cursor is not None and supported:
        next_steps.append('continue_scan')
    if not items and not disabled and not confirmed_empty:
        next_steps.extend(['try_alternative_queries', 'browse'] if searching else [])
    return {'status': 'disabled' if disabled else 'available', 'items': items,
            'next_offset': cursor, 'scanned': scanned, 'truncated': cursor is not None,
            'retrieval_method': 'lexical' if searching else 'browse',
            'match_status': 'disabled' if disabled else 'matched' if items else 'no_match',
            'confirmed_empty': confirmed_empty,
            'coverage': {'state': 'disabled' if disabled else 'partial' if partial else 'complete',
                         'start_offset': payload.offset, 'upstream_exhausted': cursor is None and not disabled,
                         'reason': reason if supported else 'upstream_offset_limit',
                         'continuation_supported': supported}, 'next_steps': next_steps}


async def recall(client, user_id, payload, *, timezone='Asia/Shanghai', scan_limit=SCAN_LIMIT,
                 max_result_bytes=RECALL_MAX_BYTES):
    """Search complete entries under the caller's scan/byte limits and retain honest continuation."""
    settings = await client.application_memory_settings(user_id)
    if type(settings.get('enabled')) is not bool:
        raise NexusProtocolError('记忆启用状态无法确认。')
    if not settings['enabled']:
        return recall_result([], payload, 0, None, 'disabled', disabled=True)
    results, scanned, cursor, result_bytes = [], 0, payload.offset, 0
    queries = [payload.query, *payload.queries] if payload.mode != 'browse' else []
    searching = any(query.strip() for query in queries)

    def response(next_offset, reason):
        """Order only the consumed candidates, preserving a cursor after every inspected source row."""
        ordered = [item for _, item in sorted(results, key=lambda pair: pair[0], reverse=True)]
        return recall_result(ordered, payload, scanned, next_offset, reason)

    while scanned < scan_limit:
        page = memory_page(await client.list_application_memory(user_id, offset=cursor, limit=min(50, scan_limit-scanned)))
        for index, item in enumerate(page['items']):
            scanned += 1
            if item.get('status') != 'active':
                continue
            projection = business_projection(item)
            if not applicable(projection, payload.purpose, timezone, payload.matter_id):
                continue
            rank, matches = lexical_match(item, projection, queries, [*payload.categories, *payload.tags])
            if searching and not matches['matched_queries']:
                continue
            text = item.get('text')
            if not isinstance(text, str):
                raise NexusProtocolError('记忆正文结构无效，未以空文本代替。')
            result = {'entry_id': item['entry_id'], 'version': item['version'],
                'entry_version':item['version'], 'provenance':item.get('source'), 'text': text, 'typed_data': item.get('typed_data'), 'business': projection, 'match': matches,
                'source': {'kind': 'assistant_memory', 'entry_id': item['entry_id'], 'version': item['version']}}
            size = len(json.dumps(result, ensure_ascii=False).encode())
            if size > max_result_bytes:
                raise NexusProtocolError('单条记忆超过读取大小限制，未截断原文或视为空结果。')
            if result_bytes + size > max_result_bytes:
                # Resume at this complete entry, including when it is the first
                # entry of the next upstream page. Never skip or trim its text.
                return response(cursor + index, 'response_size')
            result_bytes += size
            results.append((rank, result))
            if len(results) == payload.limit:
                next_offset = cursor + index + 1 if index+1 < len(page['items']) else page['next_offset']
                return response(next_offset, 'result_limit' if next_offset is not None else 'end')
        next_offset = page['next_offset']
        if next_offset is None:
            return response(None, 'end')
        if not page['items'] or next_offset <= cursor:
            raise NexusProtocolError('记忆分页没有前进，未将结果当作完整列表。')
        cursor = next_offset
        if cursor > UPSTREAM_MAX_OFFSET:
            return response(cursor, 'upstream_offset_limit')
    return response(cursor, 'scan_limit')


async def validate_sources(client, user_id, sources):
    """Check owned entries by exact ID/current history, independent of their list offset."""
    if not sources:
        return
    settings = await client.application_memory_settings(user_id)
    if settings.get('enabled') is not True:
        raise ConflictError('business_memory_disabled', '记忆已关闭或不可确认，请重新处理当前安排。')
    for entry_id, version in {source['entry_id']: source['version'] for source in sources}.items():
        history = await client.list_application_memory(user_id, history_entry_id=entry_id, limit=1)
        items = history.get('items')
        if (history.get('entry_id') != entry_id or type(history.get('current_version')) is not int
                or not isinstance(items, list) or not items or not isinstance(items[0], dict)
                or items[0].get('version') != history['current_version']):
            raise ConflictError('business_memory_unconfirmed', '所用记忆的当前版本未能确认，未执行依赖它的新操作。')
        if history['current_version'] != version or items[0].get('status') != 'active':
            raise ConflictError('business_memory_changed', '所用记忆已变化，请读取最新事实后再安排。')


async def save_user_fact(db, client, user, request, payload):
    """Preserve the original quote-save tool while using the restricted local command executor."""
    from assistant_api.services.memory_repository import agent_command
    local_id=request.scope.get('assistant_execution_run_id')
    if not local_id:
        raise ForbiddenError('business_memory_source_required','需要当前已绑定的助手任务。')
    key=request.headers.get('idempotency-key')
    if not key:
        raise InvalidRequestError('business_memory_key_required','缺少服务端执行标识。')
    fields={'tags':payload.tags}
    if payload.category:
        fields['business_category']=payload.category
    return await agent_command(db,client,user,local_id,{'action':'save','source_quote':payload.source_quote,
        'text':payload.source_quote,'app_data':AppMemoryData(fields=fields).model_dump(mode='json')},key=key)
