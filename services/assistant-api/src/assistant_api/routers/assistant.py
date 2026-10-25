"""User-facing assistant endpoints, including a resumable SSE proxy.

The proxy never invents progress: it forwards committed NexusPilot events, records
the highest sequence it saw, and turns a broken stream into an explicit error
frame instead of a quiet end that a client could mistake for completion.

Chat history starts from `user_asks`, never from `nexus_runs`. An internal run
(commute analysis today, other jobs later) has no user-ask row, so it is
invisible to the list, detail, event and cancel endpoints.
"""

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from assistant_api.core.deps import (
    CurrentUser,
    DatabaseSession,
    NexusClient,
    SettingsDependency,
)
from assistant_api.core.errors import ConflictError
from assistant_api.models import NexusRun, UserAsk
from assistant_api.nexus_client import NexusError, SseEvent
from assistant_api.services import ai_tasks, run_service
from assistant_api.services.memory_service import search_memory

router = APIRouter(prefix="/api/v1", tags=["assistant"])

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "unknown"}

# Published as an enum so a client can switch on it instead of parsing strings.
RunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "unknown"]


class AskRequest(BaseModel):
    """Submit one natural-language request; the owner comes from the session."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AskResponse(BaseModel):
    """Expose local and remote identifiers so the client can subscribe or cancel."""

    local_run_id: str
    nexus_run_id: str | None
    workflow_execution_id: str | None
    status: RunStatus
    replayed: bool = False


class AskRead(AskResponse):
    """Add the projection fields a history list and refresh need."""

    request_text: str
    last_event_sequence: int
    error_code: str | None = None
    answer: str | None = None
    completed_at: datetime | None = None
    created_at: datetime

    @field_serializer("completed_at", "created_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        """Always publish an unambiguous UTC instant, including SQLite-naive rows."""

        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MemorySearchRequest(BaseModel):
    """Search only the caller's own memory scope."""

    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1, max_length=2_000)
    memory_types: list[str] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=10, ge=1, le=50)
    idempotency_key: str = Field(min_length=8, max_length=128)


# A local record may only be classified as "chat" when the server can prove it
# has a chat association. "unknown" is a real answer, not a disguised 404.
AskClassification = Literal["chat", "not_chat", "unknown"]


class AskVerifyRequest(BaseModel):
    """Ask about local ids a client cached before history was separated."""

    model_config = ConfigDict(extra="forbid")

    local_run_ids: list[str] = Field(min_length=1, max_length=50)


class AskVerifyItem(BaseModel):
    """One verdict; never includes request text or internal instructions."""

    local_run_id: str
    classification: AskClassification


class AskVerifyResponse(BaseModel):
    results: list[AskVerifyItem]


@router.post("/asks", response_model=AskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_ask(
    payload: AskRequest,
    request: Request,
    db: DatabaseSession,
    client: NexusClient,
    settings: SettingsDependency,
    user: CurrentUser,
) -> AskResponse:
    """Start one run and its Agent workflow, or replay the run for this key."""

    row, replayed = await run_service.start_chat_ask(
        db,
        client,
        settings,
        session_factory=request.app.state.session_factory,
        user=user,
        request_text=payload.text,
        idempotency_key=payload.idempotency_key,
    )
    return AskResponse(
        local_run_id=row.local_run_id,
        nexus_run_id=row.nexus_run_id,
        workflow_execution_id=row.workflow_execution_id,
        status=row.status,
        replayed=replayed,
    )


@router.post("/asks/verify", response_model=AskVerifyResponse)
async def verify_asks(
    payload: AskVerifyRequest, db: DatabaseSession, user: CurrentUser
) -> AskVerifyResponse:
    """Classify cached local ids for the current account only.

    `chat` means a user-ask association exists; `not_chat` means the run exists,
    belongs to the caller, and is an internal task; everything else (deleted,
    another account, pre-migration row without a purpose) is `unknown`. A client
    must keep unknown records instead of deleting them.
    """

    wanted = list(dict.fromkeys(payload.local_run_ids))
    ask_ids = set(
        (
            await db.scalars(
                select(UserAsk.local_run_id).where(
                    UserAsk.user_id == user.user_id,
                    UserAsk.local_run_id.in_(wanted),
                )
            )
        ).all()
    )
    runs = {
        row.local_run_id: row
        for row in (
            await db.scalars(
                select(NexusRun).where(
                    NexusRun.user_id == user.user_id,
                    NexusRun.local_run_id.in_(wanted),
                )
            )
        ).all()
    }
    results: list[AskVerifyItem] = []
    for local_run_id in wanted:
        if local_run_id in ask_ids:
            classification: AskClassification = "chat"
        elif runs.get(local_run_id) is not None and runs[local_run_id].purpose in {
            ai_tasks.COMMUTE_INTERNAL,
        }:
            classification = "not_chat"
        else:
            classification = "unknown"
        results.append(
            AskVerifyItem(local_run_id=local_run_id, classification=classification)
        )
    return AskVerifyResponse(results=results)


@router.get("/asks", response_model=list[AskRead])
async def list_asks(
    db: DatabaseSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=50),
) -> list[AskRead]:
    """List the caller's recent chat questions, newest first."""

    rows = await run_service.list_chat_runs(db, user=user, limit=limit)
    return [_to_read(row, ask.request_text) for ask, row in rows]


@router.get("/asks/{local_run_id}", response_model=AskRead)
async def get_ask(
    local_run_id: str,
    db: DatabaseSession,
    client: NexusClient,
    user: CurrentUser,
) -> AskRead:
    """Return one chat question and reconcile status/answer when needed.

    This path is enough for a page refresh after the original SSE connection has
    gone away; it never depends on an in-memory stream. A run without a chat
    association is reported as not found, without touching the upstream.
    """

    ask, row = await run_service.get_chat_run(
        db, user=user, local_run_id=local_run_id
    )
    row = await run_service.refresh_run(db, client, user=user, row=row)
    return _to_read(row, ask.request_text)


@router.post("/asks/{local_run_id}/cancel", response_model=AskRead)
async def cancel_ask(
    local_run_id: str,
    db: DatabaseSession,
    client: NexusClient,
    user: CurrentUser,
) -> AskRead:
    """Stop future nodes for one chat run and keep the remote result visible."""

    ask = await run_service.get_user_ask(
        db, user=user, local_run_id=local_run_id
    )
    row = await run_service.cancel_chat_run(
        db, client, user=user, local_run_id=local_run_id
    )
    return _to_read(row, ask.request_text)


@router.get("/asks/{local_run_id}/events")
async def stream_ask_events(
    local_run_id: str,
    request: Request,
    db: DatabaseSession,
    client: NexusClient,
    user: CurrentUser,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int | None = Query(default=None, ge=0),
) -> StreamingResponse:
    """Forward committed events from the last acknowledged sequence onwards.

    `Last-Event-ID` wins over `after` because it is what a reconnecting browser
    sends automatically. Both are resolved before any bytes are written, so an
    invalid run never yields a half-open stream.
    """

    _ask, row = await run_service.get_chat_run(
        db, user=user, local_run_id=local_run_id
    )
    if not row.workflow_execution_id:
        raise ConflictError("run_not_streamable", "该记录还没有可订阅的执行。")

    resume_from = last_event_id or (str(after) if after is not None else None)
    workflow_id = row.workflow_execution_id
    external_user_id = user.user_id
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory

    async def event_source():
        """Yield forwarded frames and persist progress with a fresh session."""

        terminal_seen: str | None = None
        try:
            async for event in client.stream_events(
                external_user_id, workflow_id, last_event_id=resume_from
            ):
                sequence = _sequence_of(event)
                payload = _safe_payload(event)
                terminal = _terminal_status(payload)
                terminal_seen = terminal_seen or terminal
                async with factory() as progress_db:
                    await run_service.record_progress(
                        progress_db,
                        local_run_id=local_run_id,
                        event_sequence=sequence,
                        terminal_status=terminal,
                    )
                yield _format(event)
            if terminal_seen:
                # The finite replay may be the only thing an observer sees; make
                # the final answer durable even if no background observer is alive.
                async with factory() as reconcile_db:
                    stored = await run_service.get_run(
                        reconcile_db, user=user, local_run_id=local_run_id
                    )
                    await run_service.refresh_run(
                        reconcile_db, client, user=user, row=stored
                    )
        except NexusError as exc:
            async with factory() as progress_db:
                await run_service.record_progress(
                    progress_db, local_run_id=local_run_id, event_sequence=None,
                    error_code=exc.code,
                )
            body = json.dumps(
                {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
                ensure_ascii=False,
            )
            yield f"event: assistant.error\ndata: {body}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/memory/search")
async def memory_search(
    payload: MemorySearchRequest, client: NexusClient, user: CurrentUser
):
    """Return bounded memory hits for the caller, or an upstream error."""

    result = await search_memory(
        client,
        external_user_id=user.user_id,
        query_text=payload.query_text,
        idempotency_key=payload.idempotency_key,
        memory_types=payload.memory_types,
        limit=payload.limit,
    )
    return result.model_dump(mode="json")


def _to_read(row: NexusRun, request_text: str) -> AskRead:
    """Project a stored run without leaking remote identity or hidden details."""

    return AskRead(
        local_run_id=row.local_run_id,
        nexus_run_id=row.nexus_run_id,
        workflow_execution_id=row.workflow_execution_id,
        status=row.status,
        request_text=request_text,
        last_event_sequence=row.last_event_sequence,
        error_code=row.error_code,
        answer=run_service.answer_text(row),
        completed_at=row.completed_at,
        created_at=row.created_at,
    )


def _sequence_of(event: SseEvent) -> int | None:
    """Accept only a non-negative integer event id; anything else is not a cursor."""

    if event.id is None or not event.id.isdigit():
        return None
    return int(event.id)


def _safe_payload(event: SseEvent) -> dict:
    """Decode an event body for status inspection, tolerating a non-JSON frame."""

    try:
        value = json.loads(event.data)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _terminal_status(payload: dict) -> str | None:
    """Map a forwarded workflow status to a local terminal state when present."""

    for key in ("workflow_status", "status"):
        value = payload.get(key)
        if isinstance(value, str):
            mapped = run_service.map_workflow_status(value)
            if mapped in TERMINAL_STATUSES:
                return mapped
    return None


def _format(event: SseEvent) -> str:
    """Serialize one frame preserving the remote id and event name."""

    lines = []
    if event.id is not None:
        lines.append(f"id: {event.id}")
    lines.append(f"event: {event.event}")
    for chunk in event.data.split("\n"):
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"
