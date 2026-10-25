"""Minimal SSE reader that preserves the resume contract.

Only `id`, `event` and `data` are interpreted; comments and `retry` are ignored
without discarding the surrounding frame. A stream that ends before its final
blank line still yields the buffered frame, because dropping it would silently
lose the last state transition.
"""

from collections.abc import AsyncIterator

from assistant_api.nexus_client.models import SseEvent


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Yield one event per blank-line terminated frame, tolerating CRLF."""

    event_id: str | None = None
    event_type: str | None = None
    data: list[str] = []

    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield SseEvent(id=event_id, event=event_type or "message", data="\n".join(data))
            event_id, event_type, data = None, None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "id":
            event_id = value or None
        elif field == "event":
            event_type = value or None
        elif field == "data":
            data.append(value)

    if data:
        yield SseEvent(id=event_id, event=event_type or "message", data="\n".join(data))
