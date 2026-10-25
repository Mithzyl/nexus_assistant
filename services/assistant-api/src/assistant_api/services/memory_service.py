"""Read long-term memory through NexusPilot, never from a local table.

Personal Assistant has no memory store of its own. If NexusPilot cannot answer,
the feature degrades to "unknown", not to an empty list that would read as
"nothing was ever recorded".
"""

from assistant_api.nexus_client import MemoryQueryRequest, NexusPilotClient
from assistant_api.nexus_client.models import MemoryQueryResponse


async def search_memory(
    client: NexusPilotClient,
    *,
    external_user_id: str,
    query_text: str,
    idempotency_key: str,
    memory_types: list[str] | None = None,
    limit: int = 20,
) -> MemoryQueryResponse:
    """Return bounded hits scoped to one user; an empty result is a valid answer."""

    return await client.query_memory(
        external_user_id,
        MemoryQueryRequest(
            query_text=query_text,
            memory_types=memory_types or [],
            limit=limit,
            idempotency_key=idempotency_key,
        ),
    )
