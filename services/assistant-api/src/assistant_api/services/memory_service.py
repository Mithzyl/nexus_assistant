"""Search Assistant-owned long-term memory; unavailable storage never means an empty account."""

from sqlalchemy import select

from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import NexusRun
from assistant_api.services.memory_repository import MemoryStore
from assistant_api.nexus_client.models import MemoryHit, MemoryQueryResponse


async def validate_optional_context(db, user_id: str, local_run_id: str | None) -> None:
    """Keep legacy source references owner-scoped without making a Run mandatory."""
    if local_run_id is not None:
        row = await db.scalar(select(NexusRun).where(NexusRun.local_run_id == local_run_id, NexusRun.user_id == user_id))
        if row is None:
            raise NotFoundError('memory_run_not_found', '未找到当前账号的来源记录。')


async def resolve_memory_run(db, user_id: str, local_run_id: str | None = None) -> str:
    """Resolve ownership locally; never accept a caller-supplied Nexus Run or owner."""
    statement = select(NexusRun).where(NexusRun.user_id == user_id)
    if local_run_id:
        statement = statement.where(NexusRun.local_run_id == local_run_id)
    else:
        statement = statement.where(NexusRun.nexus_run_id.is_not(None))
    row = await db.scalar(statement.order_by(NexusRun.created_at.desc()).limit(1))
    if local_run_id and row is None:
        raise NotFoundError("memory_run_not_found", "未找到当前账号的会话记录。")
    if row is None or not row.nexus_run_id:
        raise ConflictError("memory_context_required", "请先完成一次助手请求以建立记忆归属；当前还不能确认上游记忆状态。")
    return row.nexus_run_id


async def search_memory(
    client: MemoryStore,
    *,
    db,
    external_user_id: str,
    query_text: str,
    idempotency_key: str,
    memory_types: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    local_run_id: str | None = None,
) -> MemoryQueryResponse:
    """Search open text; legacy type labels only rank hits, never exclude another domain."""
    from assistant_api.services.business_memory import memory_page, business_projection, lexical_match, SCAN_LIMIT, UPSTREAM_MAX_OFFSET
    hints = memory_types or []
    if any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in hints):
        raise InvalidRequestError('memory_label_invalid', '记忆标签须为 1 至 100 字符的文字。')
    await validate_optional_context(db, external_user_id, local_run_id)
    items, scanned, cursor = [], 0, offset
    next_offset = cursor

    def response(next_offset, reason):
        """Keep legacy pagination while explicitly reporting bounded or noninitial search coverage."""
        supported = next_offset is None or next_offset <= UPSTREAM_MAX_OFFSET
        next_steps = ['continue_scan'] if next_offset is not None and supported else []
        if not items:
            next_steps.extend(['try_alternative_queries', 'browse'])
        return MemoryQueryResponse(items=[hit for _, hit in sorted(items, key=lambda pair: pair[0], reverse=True)],
            next_cursor=str(next_offset) if next_offset is not None else None, scanned=scanned,
            truncated=next_offset is not None, match_status='matched' if items else 'no_match',
            retrieval_method='lexical', next_steps=next_steps,
            coverage={'state': 'partial' if next_offset is not None or offset > 0 else 'complete',
                'start_offset': offset, 'upstream_exhausted': next_offset is None,
                'reason': reason if supported else 'upstream_offset_limit', 'continuation_supported': supported})

    while scanned < SCAN_LIMIT:
        body = memory_page(await client.list_application_memory(external_user_id, offset=cursor, limit=min(50, SCAN_LIMIT-scanned)))
        for index, item in enumerate(body['items']):
            scanned += 1
            projection = business_projection(item)
            typed = item.get('typed_data') or {}
            kind = typed.get('memory_type') or item.get('memory_type', 'free_text')
            rank, match = lexical_match(item, projection, [query_text], hints)
            if not match['matched_queries']:
                continue
            items.append((rank, MemoryHit(memory_id=item['entry_id'], memory_type=kind,
                content=item.get('text', ''), status=item.get('status'), version=item['version'],
                sensitive=item.get('sensitive', False), app_data=item.get('app_data'),
                typed_data=item.get('typed_data'), business=projection)))
            if len(items) == limit:
                next_offset = cursor+index+1 if index+1 < len(body['items']) else body['next_offset']
                return response(next_offset, 'result_limit' if next_offset is not None else 'end')
        next_offset = body['next_offset']
        if next_offset is None:
            break
        if next_offset <= cursor or not body['items']:
            from assistant_api.nexus_client.errors import NexusProtocolError
            raise NexusProtocolError('记忆分页没有前进。')
        cursor = next_offset
        if cursor > UPSTREAM_MAX_OFFSET:
            break
    return response(next_offset, 'end' if next_offset is None else 'scan_limit')
