"""Create, observe and cancel AI runs on behalf of one local user.

This module is the single AI task service. It owns the local run row, the Nexus
call, the background observer and recovery; per-purpose input preparation and
result checking live in :mod:`assistant_api.services.ai_tasks`.

The local row is the durable projection a client can refresh after closing the
page. Where local and remote state disagree, NexusPilot is authoritative; the
background workflow observer and the read path both reconcile from it.

Only two callers create a run: chat (`start_chat_ask`, which also writes the
`user_asks` association) and an internal job such as commute analysis
(`create_ai_task`, which never writes that association). Chat history is read
from `user_asks`, so an internal run cannot leak into it.
"""

from __future__ import annotations

import logging

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_api.core.config import Settings
from assistant_api.core.errors import AssistantError, ConflictError, NotFoundError
from assistant_api.models import NexusRun, User, UserAsk
from assistant_api.models.base import new_id
from assistant_api.nexus_client import (
    NexusError,
    NexusPilotClient,
    NexusProtocolError,
    NexusTimeoutError,
    RoleBinding,
    RunCreateRequest,
    WorkflowCreateRequest,
    WorkflowView,
)
from assistant_api.services import ai_tasks

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "unknown"}
WORKFLOW_STATUS_MAP = {
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "outcome_unknown": "unknown",
}
ALLOWED_ROLES = {"controller", "planner", "researcher", "reviewer", "verifier"}

_background_tasks: set[asyncio.Task] = set()
_workflow_tasks: dict[str, asyncio.Task] = {}


logger = logging.getLogger(__name__)

def _track(task: asyncio.Task, local_run_id: str) -> None:
    _background_tasks.add(task)
    _workflow_tasks[local_run_id] = task

    def _forget(done: asyncio.Task) -> None:
        _background_tasks.discard(done)
        if _workflow_tasks.get(local_run_id) is done:
            _workflow_tasks.pop(local_run_id, None)

    task.add_done_callback(_forget)


async def shutdown_background_tasks() -> None:
    """Cancel in-process workflow observers before the app disposes its engine."""

    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _workflow_tasks.clear()


def map_workflow_status(status: str | None) -> str | None:
    """Map the current workflow enum to the local public RunStatus."""

    if status is None:
        return None
    if status in TERMINAL_STATUSES:
        return status
    return WORKFLOW_STATUS_MAP.get(status)


def answer_text(row: NexusRun) -> str | None:
    """Return only the public final text, never hidden result payloads."""

    if not isinstance(row.result_json, dict):
        return None
    value = row.result_json.get("final_text")
    return value if isinstance(value, str) and value else None


def workflow_request(settings: Settings, idempotency_key: str) -> WorkflowCreateRequest:
    """Build the current general-agent request from server-side configuration."""

    try:
        raw = json.loads(settings.nexus_role_bindings_json.get_secret_value())
        if not isinstance(raw, dict) or not raw:
            raise ValueError("role bindings must be a non-empty object")
        bindings: dict[str, RoleBinding] = {}
        for role, value in raw.items():
            if role not in ALLOWED_ROLES:
                raise ValueError("unknown role")
            bindings[role] = RoleBinding.model_validate(value)
        if "controller" not in bindings:
            raise ValueError("controller binding is required")
        if settings.nexus_review_policy != "never" and "reviewer" not in bindings:
            raise ValueError("reviewer binding is required by the review policy")
        return WorkflowCreateRequest(
            workflow_name=settings.nexus_workflow_name,
            workflow_version=settings.nexus_workflow_version,
            execution_profile=settings.nexus_execution_profile,
            idempotency_key=idempotency_key,
            role_bindings=bindings,
            review_policy=settings.nexus_review_policy,
            stream=True,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AssistantError(
            "nexus_workflow_configuration_invalid",
            "服务端模型配置无效。",
            500,
        ) from exc


async def _find_by_key(
    db: AsyncSession, *, user_id: str, idempotency_key: str
) -> NexusRun | None:
    return await db.scalar(
        select(NexusRun).where(
            NexusRun.user_id == user_id,
            NexusRun.idempotency_key == idempotency_key,
        )
    )


def _adopt_task_metadata(
    row: NexusRun,
    *,
    purpose: str,
    trigger: str,
    input_version: int,
    business_ref: str | None,
) -> None:
    """Fill server-owned task metadata on an existing row, never overwrite purpose."""

    if row.purpose is None:
        row.purpose = purpose
    if row.trigger is None:
        row.trigger = trigger
    if row.input_version is None:
        row.input_version = input_version
    if row.business_ref is None and business_ref is not None:
        row.business_ref = business_ref


def _check_replay(
    existing: NexusRun,
    *,
    purpose: str,
    input_text: str,
    strict_input_match: bool,
) -> None:
    """Refuse a key that belongs to another purpose or to different input."""

    if existing.purpose is not None and existing.purpose != purpose:
        raise ConflictError(
            "idempotency_key_reserved",
            "同一幂等键已用于另一类任务，请为新任务使用新的幂等键。",
        )
    if strict_input_match and existing.request_text != input_text:
        raise ConflictError(
            "idempotency_conflict",
            "同一幂等键已对应另一条请求内容，请为新问题使用新的幂等键。",
        )


async def create_ai_task(
    db: AsyncSession,
    *,
    user: User,
    purpose: str,
    trigger: str,
    idempotency_key: str,
    context: dict[str, Any],
    business_ref: str | None = None,
    attach: Callable[[NexusRun], None] | None = None,
) -> tuple[NexusRun, bool]:
    """Return the run for this key, creating the local row exactly once.

    Local writes (run row, chat association or business link) are committed here,
    before any network call. The caller launches or resumes the remote workflow
    afterwards, so a slow upstream never holds a write transaction open.
    """

    spec = ai_tasks.spec_for(purpose)
    if trigger not in ai_tasks.VALID_TRIGGERS:  # pragma: no cover - programming error
        raise ValueError(f"unknown AI task trigger: {trigger!r}")
    input_text = spec.prepare_input(context)

    existing = await _find_by_key(
        db, user_id=user.user_id, idempotency_key=idempotency_key
    )
    if existing is not None:
        _check_replay(
            existing,
            purpose=purpose,
            input_text=input_text,
            strict_input_match=spec.strict_input_match,
        )
        _adopt_task_metadata(
            existing,
            purpose=purpose,
            trigger=trigger,
            input_version=spec.input_version,
            business_ref=business_ref,
        )
        await db.commit()
        return existing, True

    row = NexusRun(
        local_run_id=new_id(),
        user_id=user.user_id,
        nexus_principal_id=None,
        idempotency_key=idempotency_key,
        request_text=input_text,
        status="pending",
        purpose=purpose,
        business_ref=business_ref,
        input_version=spec.input_version,
        trigger=trigger,
    )
    db.add(row)
    if attach is not None:
        attach(row)
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent submit won the unique key (or the association). Reuse it
        # rather than returning an error or creating a second remote Run.
        await db.rollback()
        existing = await _find_by_key(
            db, user_id=user.user_id, idempotency_key=idempotency_key
        )
        if existing is None:
            raise
        _check_replay(
            existing,
            purpose=purpose,
            input_text=input_text,
            strict_input_match=spec.strict_input_match,
        )
        _adopt_task_metadata(
            existing,
            purpose=purpose,
            trigger=trigger,
            input_version=spec.input_version,
            business_ref=business_ref,
        )
        await db.commit()
        return existing, True
    await db.refresh(row)
    return row, False


async def launch_ai_task(
    db: AsyncSession,
    client: NexusPilotClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user: User,
    row: NexusRun,
) -> NexusRun:
    """Create the remote run after the local row is committed, then observe it.

    The create call is the only step that can leave `unknown`: a timeout means the
    remote side may exist, so it is recorded and never retried automatically.
    """

    # End any open read transaction before the network call: a SQLite SHARED lock
    # must not be held while waiting on NexusPilot. All local writes are already
    # committed by create_ai_task.
    await db.commit()
    if not row.nexus_run_id:
        try:
            run = await client.create_run(
                user.user_id, RunCreateRequest(user_request=row.request_text)
            )
        except NexusTimeoutError as exc:
            await _finish(db, row, status="unknown", error_code=exc.code)
            raise
        except NexusError as exc:
            await _finish(db, row, status="failed", error_code=exc.code)
            raise
        row.nexus_run_id = run.run_id
        await db.commit()
    return await _start_workflow(
        db,
        client,
        settings,
        session_factory,
        row=row,
        external_user_id=user.user_id,
    )


async def _start_workflow(
    db: AsyncSession,
    client: NexusPilotClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    row: NexusRun,
    external_user_id: str,
) -> NexusRun:
    """Start observing one workflow for an already-created remote Run.

    The workflow request carries a deterministic idempotency key. Recovery uses
    the saved run id and the same key; it never creates a second Nexus Run.
    """

    try:
        payload = workflow_request(settings, f"{row.idempotency_key}:workflow")
    except AssistantError as exc:
        await _finish(db, row, status="failed", error_code=exc.code)
        raise
    ready: asyncio.Future[WorkflowView] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(
        _consume_workflow(
            client=client,
            session_factory=session_factory,
            local_run_id=row.local_run_id,
            external_user_id=external_user_id,
            remote_run_id=row.nexus_run_id,
            payload=payload,
            ready=ready,
        )
    )
    _track(task, row.local_run_id)
    try:
        await asyncio.wait_for(
            ready, timeout=settings.nexus_workflow_start_timeout_seconds
        )
    except TimeoutError as exc:
        await _mark_unknown(
            db,
            row,
            code="nexus_workflow_start_unknown",
            message="工作流启动结果未知，请刷新状态。",
        )
        raise NexusTimeoutError("NexusPilot workflow start outcome is unknown") from exc
    except NexusError:
        await db.refresh(row)
        raise
    await db.refresh(row)
    return row


async def resume_ai_task(
    db: AsyncSession,
    client: NexusPilotClient,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user: User,
    row: NexusRun,
) -> NexusRun:
    """Reconcile an existing local run from its saved identifiers.

    Ordering matters: a saved remote run id is authoritative. Only when no run
    id exists do we report `unknown` and refuse to create a replacement,
    because the local idempotency key alone cannot prove the remote outcome.
    """

    if row.status == "cancelled":
        return row
    if not row.nexus_run_id:
        if row.status not in TERMINAL_STATUSES:
            await _finish(
                db, row, status="unknown", error_code="nexus_run_mapping_unknown"
            )
        return row
    if row.workflow_execution_id:
        return await refresh_run(db, client, user=user, row=row)
    active = _workflow_tasks.get(row.local_run_id)
    if active is not None and not active.done():
        return row
    try:
        await _start_workflow(
            db, client, settings, session_factory,
            row=row, external_user_id=user.user_id,
        )
    except (AssistantError, NexusError):
        # The observer records a truthful unknown/failed state; never fall back
        # to a second remote Run or to a fabricated result.
        await db.refresh(row)
    return row


async def wait_terminal(
    db: AsyncSession,
    client: NexusPilotClient,
    *,
    user: User,
    row: NexusRun,
    timeout_seconds: float,
    poll_seconds: float = 1.0,
) -> tuple[NexusRun, bool]:
    """Poll the saved identifiers until terminal; return (row, timed_out)."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while row.status not in TERMINAL_STATUSES:
        if loop.time() >= deadline:
            return row, True
        await asyncio.sleep(poll_seconds)
        row = await refresh_run(db, client, user=user, row=row)
    return row, False


async def get_run(db: AsyncSession, *, user: User, local_run_id: str) -> NexusRun:
    """Read one run only when it belongs to the authenticated user."""

    row = await db.scalar(
        select(NexusRun).where(
            NexusRun.local_run_id == local_run_id, NexusRun.user_id == user.user_id
        )
    )
    if row is None:
        raise NotFoundError("run_not_found", "记录不存在。")
    return row


async def get_user_ask(
    db: AsyncSession, *, user: User, local_run_id: str
) -> UserAsk:
    """Return the chat association; a run without one is not chat history."""

    ask = await db.scalar(
        select(UserAsk).where(
            UserAsk.user_id == user.user_id,
            UserAsk.local_run_id == local_run_id,
        )
    )
    if ask is None:
        raise NotFoundError("run_not_found", "记录不存在。")
    return ask


async def get_chat_run(
    db: AsyncSession, *, user: User, local_run_id: str
) -> tuple[UserAsk, NexusRun]:
    """Read one chat record: the association must exist before the run is read."""

    ask = await get_user_ask(db, user=user, local_run_id=local_run_id)
    row = await get_run(db, user=user, local_run_id=local_run_id)
    return ask, row


async def refresh_run(
    db: AsyncSession, client: NexusPilotClient, *, user: User, row: NexusRun
) -> NexusRun:
    """Reconcile status and final answer from NexusPilot after a reconnect.

    This is what makes a page refresh independent of the original SSE connection.
    A transient upstream failure leaves the last durable local projection intact.
    """

    if not row.workflow_execution_id:
        return row
    if row.status == "cancelled":
        # A cancellation already accepted by NexusPilot is terminal for this
        # local projection; do not resurrect it from a late-successful stream.
        return row
    needs_result = not (row.status in TERMINAL_STATUSES and answer_text(row))
    if row.status in TERMINAL_STATUSES and not needs_result:
        return row
    # No local write is pending here; release the read transaction before the
    # network round trip so other processes can write.
    await db.commit()
    try:
        summary = await client.get_workflow_summary(
            user.user_id, row.workflow_execution_id
        )
    except NexusError:
        return row
    status = map_workflow_status(summary.status)
    if status:
        row.status = status
        if status in TERMINAL_STATUSES:
            row.completed_at = datetime.now(UTC)
        await db.commit()
    if row.status in TERMINAL_STATUSES and not answer_text(row):
        await _persist_result(
            db,
            client,
            external_user_id=user.user_id,
            workflow_id=row.workflow_execution_id,
            row=row,
        )
    await db.refresh(row)
    return row


async def cancel_run(
    db: AsyncSession, client: NexusPilotClient, *, user: User, local_run_id: str
) -> NexusRun:
    """Cancel the caller's workflow through the current App API."""

    row = await get_run(db, user=user, local_run_id=local_run_id)
    return await _cancel_row(db, client, user=user, row=row)


async def cancel_chat_run(
    db: AsyncSession, client: NexusPilotClient, *, user: User, local_run_id: str
) -> NexusRun:
    """Cancel only a chat record; an internal task is not reachable this way."""

    _ask, row = await get_chat_run(db, user=user, local_run_id=local_run_id)
    return await _cancel_row(db, client, user=user, row=row)


async def _cancel_row(
    db: AsyncSession, client: NexusPilotClient, *, user: User, row: NexusRun
) -> NexusRun:
    if row.status in {"succeeded", "failed", "cancelled"}:
        # Cancellation is not a time machine: a finished run keeps its true
        # terminal state, and a repeated cancel is an idempotent read.
        return row
    if not row.workflow_execution_id:
        raise ConflictError("run_not_cancellable", "该记录当前不可取消。")

    await db.commit()
    summary = await client.cancel_workflow(user.user_id, row.workflow_execution_id)
    status = map_workflow_status(summary.status)
    if status:
        row.status = status
        if status in TERMINAL_STATUSES:
            row.completed_at = datetime.now(UTC)
    await db.commit()
    if row.status == "succeeded":
        await _persist_result(
            db,
            client,
            external_user_id=user.user_id,
            workflow_id=row.workflow_execution_id,
            row=row,
        )
    await db.refresh(row)
    return row


async def record_progress(
    db: AsyncSession,
    *,
    local_run_id: str,
    event_sequence: int | None,
    terminal_status: str | None = None,
    error_code: str | None = None,
) -> None:
    """Persist the highest observed event sequence and any terminal state."""

    row = await db.get(NexusRun, local_run_id)
    if row is None:
        return
    if event_sequence is not None and event_sequence > row.last_event_sequence:
        row.last_event_sequence = event_sequence
    if terminal_status and row.status != "cancelled":
        previous = row.status
        row.status = terminal_status
        if terminal_status in TERMINAL_STATUSES and (
            row.completed_at is None
            or previous not in TERMINAL_STATUSES
            or (previous == "unknown" and terminal_status != "unknown")
        ):
            # A finite replay of an already-finished run must keep its original
            # completion instant; only a real transition may stamp a new one.
            row.completed_at = datetime.now(UTC)
    if error_code:
        row.error_code = error_code
    await db.commit()


async def _consume_workflow(
    *,
    client: NexusPilotClient,
    session_factory: async_sessionmaker[AsyncSession],
    local_run_id: str,
    external_user_id: str,
    remote_run_id: str,
    payload: WorkflowCreateRequest,
    ready: asyncio.Future[WorkflowView],
) -> None:
    """Observe the workflow's live SSE stream and durably record its outcome.

    The stream's first frame carries the workflow id, which unblocks the caller.
    Continued consumption is what stores the terminal status and final answer
    while the original HTTP request is already gone.
    """

    workflow_id: str | None = None
    terminal_status: str | None = None
    try:
        async for event in client.start_workflow_events(
            external_user_id, remote_run_id, payload
        ):
            data = client.event_payload(event)
            event_workflow_id = data.get("workflow_execution_id")
            if workflow_id is None and isinstance(event_workflow_id, str) and event_workflow_id:
                workflow_id = event_workflow_id
                await _record_workflow_started(
                    session_factory,
                    local_run_id=local_run_id,
                    workflow_id=workflow_id,
                    remote_status=str(data.get("workflow_status") or "running"),
                )
                if not ready.done():
                    ready.set_result(
                        WorkflowView(
                            workflow_execution_id=workflow_id,
                            run_id=remote_run_id,
                            status=str(data.get("workflow_status") or "running"),
                        )
                    )
            sequence = _sequence_of_event(event.id)
            event_status = map_workflow_status(data.get("workflow_status"))
            # Do not publish a terminal status until the final answer (or a
            # definitive failure) is persisted; a refresh must never observe
            # "succeeded" with no answer merely because two commits raced.
            async with session_factory() as db:
                await record_progress(
                    db,
                    local_run_id=local_run_id,
                    event_sequence=sequence,
                    terminal_status=None,
                    error_code=(
                        _event_error_code(data)
                        if event_status in {"failed", "unknown"}
                        else None
                    ),
                )
            if event_status in TERMINAL_STATUSES:
                terminal_status = event_status
                break
        if workflow_id is None:
            raise NexusProtocolError("workflow stream did not expose a workflow id")
        if terminal_status not in TERMINAL_STATUSES:
            async with session_factory() as db:
                await record_progress(
                    db,
                    local_run_id=local_run_id,
                    event_sequence=None,
                    terminal_status="unknown",
                    error_code="nexus_event_stream_incomplete",
                )
            return
        async with session_factory() as db:
            row = await db.get(NexusRun, local_run_id)
            if row is not None:
                await _persist_result(
                    db,
                    client,
                    external_user_id=external_user_id,
                    workflow_id=workflow_id,
                    row=row,
                    fallback_status=terminal_status,
                )
    except NexusError as exc:
        async with session_factory() as db:
            row = await db.get(NexusRun, local_run_id)
            if row is not None:
                await _finish(
                    db,
                    row,
                    status="unknown" if workflow_id else "failed",
                    error_code=exc.code,
                )
        if not ready.done():
            ready.set_exception(exc)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a background observer must not crash the app
        logger.exception("workflow observer failed for %s", local_run_id)
        async with session_factory() as db:
            row = await db.get(NexusRun, local_run_id)
            if row is not None:
                await _finish(
                    db,
                    row,
                    status="unknown" if workflow_id else "failed",
                    error_code="nexus_protocol_error",
                )
        if not ready.done():
            ready.set_exception(NexusProtocolError("workflow observer failed"))


async def _record_workflow_started(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    local_run_id: str,
    workflow_id: str,
    remote_status: str,
) -> None:
    async with session_factory() as db:
        row = await db.get(NexusRun, local_run_id)
        if row is None:
            return
        row.workflow_execution_id = workflow_id
        mapped = map_workflow_status(remote_status)
        if mapped:
            row.status = mapped
        elif row.status == "pending":
            row.status = "running"
        await db.commit()


def _sequence_of_event(event_id: str | None) -> int | None:
    """Return the integer SSE sequence, or None when the id is absent/opaque.

    The event id is the resume cursor; a malformed id must not stop the stream
    from being consumed, it only means this frame carries no sequence number.
    """

    if not event_id:
        return None
    try:
        return int(str(event_id).strip())
    except (TypeError, ValueError):
        return None


def _event_error_code(payload: dict[str, Any]) -> str | None:
    """Best-effort public error code from a terminal event payload.

    Upstream nests the code differently across builds; this only ever reads a
    bounded string and never re-raises while storing an outcome.
    """

    candidates: list[Any] = [payload.get("error_code")]
    error = payload.get("error")
    if isinstance(error, dict):
        candidates.append(error.get("code"))
    public = payload.get("public_payload")
    if isinstance(public, dict):
        candidates.append(public.get("error_code"))
        inner = public.get("error")
        if isinstance(inner, dict):
            candidates.append(inner.get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:64]
    return None


async def _persist_result(
    db: AsyncSession,
    client: NexusPilotClient,
    *,
    external_user_id: str,
    workflow_id: str,
    row: NexusRun,
    fallback_status: str | None = None,
) -> None:
    """Fetch and store the public final answer once the workflow is terminal.

    The terminal status and answer are committed together, so a refresh cannot
    see a completed run with a missing answer while the result request is in
    flight. If the result is unavailable, the status from the event is still kept.
    """

    if row.status == "cancelled":
        return
    await db.commit()
    try:
        result = await client.get_workflow_result(external_user_id, workflow_id)
    except NexusError:
        if fallback_status:
            row.status = fallback_status
            row.completed_at = datetime.now(UTC)
            await db.commit()
        return
    status = map_workflow_status(result.status)
    if status:
        row.status = status
    final_output = result.final_output if isinstance(result.final_output, dict) else {}
    final_text = final_output.get("final_text")
    payload: dict[str, Any] = {"workflow_status": result.status}
    if isinstance(final_text, str) and final_text:
        payload["final_text"] = final_text
    error = result.error if isinstance(result.error, dict) else None
    if error and isinstance(error.get("error_code"), str):
        row.error_code = error["error_code"]
        payload["error_code"] = error["error_code"]
    row.result_json = payload
    if row.status in TERMINAL_STATUSES:
        row.completed_at = datetime.now(UTC)
    await db.commit()


async def _mark_unknown(
    db: AsyncSession, row: NexusRun, *, code: str, message: str
) -> None:
    await db.refresh(row)
    if row.status in TERMINAL_STATUSES:
        return
    row.status = "unknown"
    row.error_code = code
    await db.commit()


async def _finish(
    db: AsyncSession, row: NexusRun, *, status: str, error_code: str
) -> None:
    """Store a terminal or unknown local state without inventing a remote result."""

    await db.refresh(row)
    if row.status == "cancelled":
        # A cancellation accepted by NexusPilot stays terminal even if a late
        # stream error or frame arrives from the old execution.
        return
    row.status = status
    row.error_code = error_code
    if status != "unknown":
        row.completed_at = datetime.now(UTC)
    await db.commit()


async def _ensure_user_ask(
    db: AsyncSession, *, user: User, row: NexusRun, request_text: str
) -> UserAsk:
    """Make sure one chat run has its association, including adopted legacy rows."""

    ask = await db.scalar(select(UserAsk).where(UserAsk.local_run_id == row.local_run_id))
    if ask is not None:
        if row.business_ref != ask.ask_id:
            row.business_ref = ask.ask_id
            await db.commit()
        return ask
    ask = UserAsk(
        ask_id=new_id(),
        user_id=user.user_id,
        local_run_id=row.local_run_id,
        request_text=request_text,
    )
    db.add(ask)
    row.business_ref = ask.ask_id
    await db.commit()
    await db.refresh(ask)
    return ask


async def start_chat_ask(
    db: AsyncSession,
    client: NexusPilotClient,
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    user: User,
    request_text: str,
    idempotency_key: str,
) -> tuple[NexusRun, bool]:
    """Submit one chat question: association + run first, Nexus call afterwards."""

    ask_id = new_id()

    def attach(row: NexusRun) -> None:
        row.business_ref = ask_id
        db.add(
            UserAsk(
                ask_id=ask_id,
                user_id=user.user_id,
                local_run_id=row.local_run_id,
                request_text=request_text,
            )
        )

    row, replayed = await create_ai_task(
        db,
        user=user,
        purpose=ai_tasks.CHAT,
        trigger=ai_tasks.TRIGGER_CHAT,
        idempotency_key=idempotency_key,
        context={"text": request_text},
        business_ref=ask_id,
        attach=attach,
    )
    await _ensure_user_ask(db, user=user, row=row, request_text=row.request_text)
    if replayed:
        return await resume_ai_task(
            db, client, settings, session_factory, user=user, row=row
        ), True
    return await launch_ai_task(
        db, client, settings, session_factory, user=user, row=row
    ), False


async def list_chat_runs(
    db: AsyncSession, *, user: User, limit: int
) -> list[tuple[UserAsk, NexusRun]]:
    """List chat history from the association, newest first."""

    rows = (
        await db.execute(
            select(UserAsk, NexusRun)
            .join(NexusRun, NexusRun.local_run_id == UserAsk.local_run_id)
            .where(UserAsk.user_id == user.user_id)
            .order_by(UserAsk.created_at.desc(), UserAsk.ask_id.desc())
            .limit(limit)
        )
    ).all()
    return [(ask, run) for ask, run in rows]


__all__ = [
    "answer_text",
    "cancel_chat_run",
    "cancel_run",
    "create_ai_task",
    "get_chat_run",
    "get_run",
    "get_user_ask",
    "launch_ai_task",
    "list_chat_runs",
    "map_workflow_status",
    "record_progress",
    "refresh_run",
    "resume_ai_task",
    "shutdown_background_tasks",
    "start_chat_ask",
    "wait_terminal",
    "workflow_request",
]
