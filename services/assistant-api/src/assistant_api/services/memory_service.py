"""Read long-term memory through NexusPilot, never from a local table.

Personal Assistant has no memory store of its own. If NexusPilot cannot answer,
the feature degrades to "unknown", not to an empty list that would read as
"nothing was ever recorded".
"""

from sqlalchemy import select

from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.models import NexusRun
from assistant_api.nexus_client import NexusPilotClient
from assistant_api.nexus_client.models import MemoryHit, MemoryQueryResponse


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
    client: NexusPilotClient,
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
    """Return bounded hits scoped to one user; an empty result is a valid answer."""

    if memory_types:
        raise InvalidRequestError("memory_type_unsupported", "新版记忆不支持旧版类型筛选。")
    run_id = await resolve_memory_run(db, external_user_id, local_run_id) if local_run_id else None
    body = await client.list_product_memory(external_user_id, run_id, offset=offset, limit=limit)
    items = []
    for item in body["items"]:
        if query_text.casefold() not in item.get("text", "").casefold():
            continue
        items.append(MemoryHit(
            memory_id=item["entry_id"], memory_type="personal", content=item.get("text", ""),
            source_reference=None, status=item["status"], version=item["version"],
            sensitive=item["sensitive"], app_data=item.get("app_data"),
        ))
    next_offset = body.get("next_offset")
    return MemoryQueryResponse(items=items, next_cursor=str(next_offset) if next_offset is not None else None)
