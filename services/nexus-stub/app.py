"""Executable current-contract stub of the NexusPilot App API.

This is a contract fixture, not a NexusPilot implementation. It implements the
headers, run body, general-agent workflow body, finite SSE replay, result and
cancel paths used by the current server, so the Personal Assistant backend can
be exercised over real HTTP.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

APP_ID = os.environ.get("STUB_APP_ID", "personal_assistant")
APP_TOKEN = os.environ.get("STUB_APP_TOKEN", "stub-application-token-32-characters")
STREAM_DELAY = float(os.environ.get("STUB_STREAM_DELAY_MS", "70")) / 1000
FINAL_TEXT = "已记住：昨天下午理发，35 元。"

app = FastAPI(title="NexusPilot current App API stub", version="0.2.0")
RUNS: dict[str, dict[str, Any]] = {}
WORKFLOWS: dict[str, dict[str, Any]] = {}
CALLS: list[dict[str, Any]] = []
COUNTER = {"run": 0, "workflow": 0}
# Test-only control: hold a started workflow before its terminal frame so the
# UI can exercise stop and account-switch races deterministically.
HOLD_MODE = {"enabled": False}
UNKNOWN_ONCE = {"remaining": 0, "hold": False}
RELEASE_EVENTS: dict[str, asyncio.Event] = {}


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunBody(StrictBody):
    user_request: str = Field(min_length=1, max_length=100_000)


class RoleBinding(StrictBody):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)


class WorkflowBody(StrictBody):
    workflow_name: str
    workflow_version: str
    execution_profile: str
    idempotency_key: str = Field(min_length=8, max_length=128)
    role_bindings: dict[str, RoleBinding]
    review_policy: str = "never"
    stream: bool = True


class MemoryBody(StrictBody):
    query_text: str = Field(min_length=1, max_length=2_000)
    memory_types: list[str] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=20, ge=1, le=100)
    token_budget: int = Field(default=2_000, ge=1, le=20_000)
    idempotency_key: str = Field(min_length=8, max_length=128)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": message, "error": {"code": code, "message": message}},
    )


def _record(request: Request, body: str) -> None:
    CALLS.append(
        {
            "method": request.method,
            "path": request.url.path,
            "headers": {
                key: value
                for key, value in request.headers.items()
                if key.lower() in {
                    "authorization",
                    "x-application-id",
                    "x-external-user-id",
                    "last-event-id",
                }
            },
            "body": body,
        }
    )


async def _authorize(request: Request) -> tuple[str, str] | JSONResponse:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or token != APP_TOKEN:
        return _error(401, "application_unauthenticated", "应用凭据无效。")
    application_id = request.headers.get("x-application-id")
    if application_id != APP_ID:
        return _error(401, "application_unauthenticated", "应用标识与凭据不匹配。")
    subject = request.headers.get("x-external-user-id", "")
    if not 1 <= len(subject) <= 128:
        return _error(422, "invalid_request", "缺少外部用户断言。")
    return application_id, subject


def _run_events(run_id: str, workflow_id: str) -> list[dict[str, Any]]:
    events = [
        (1, "agent.workflow.started", "running", {}),
        (2, "agent.node.completed", "running", {"node_key": "controller"}),
        (3, "agent.node.completed", "running", {"node_key": "worker"}),
        (4, "agent.node.completed", "running", {"node_key": "reviewer"}),
        (
            5,
            "agent.workflow.completed",
            "completed",
            {"result_path": f"/api/v1/app/agent-workflows/{workflow_id}/result"},
        ),
    ]
    return [
        {
            "sequence": sequence,
            "type": event_type,
            "payload": {
                "event_sequence": sequence,
                "event_type": event_type,
                "workflow_execution_id": workflow_id,
                "run_id": run_id,
                "workflow_status": workflow_status,
                "public_payload": payload,
            },
        }
        for sequence, event_type, workflow_status, payload in events
    ]


def _frame(event: dict[str, Any]) -> str:
    return (
        f"id: {event['sequence']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['payload'], ensure_ascii=False)}\n\n"
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "nexus-stub", "contract": "app-v1-current"}


@app.get("/_debug/calls")
async def debug_calls() -> dict[str, Any]:
    return {"count": len(CALLS), "calls": CALLS}


@app.delete("/_debug/calls")
async def clear_calls() -> dict[str, int]:
    CALLS.clear()
    return {"count": 0}


@app.post("/_debug/hold")
async def debug_hold(enabled: bool = True) -> dict[str, Any]:
    HOLD_MODE["enabled"] = enabled
    return {"hold": enabled}


@app.get("/_debug/unknown")
async def debug_unknown() -> dict[str, Any]:
    return {
        "unknown": dict(UNKNOWN_ONCE),
        "workflows": {k: v["status"] for k, v in WORKFLOWS.items()},
    }


@app.post("/_debug/unknown_once")
async def debug_unknown_once() -> dict[str, Any]:
    UNKNOWN_ONCE["remaining"] = 1
    UNKNOWN_ONCE["hold"] = False
    return {"unknown_once": True}


@app.post("/_debug/unknown_hold")
async def debug_unknown_hold() -> dict[str, Any]:
    UNKNOWN_ONCE["remaining"] = 1
    UNKNOWN_ONCE["hold"] = True
    return {"unknown_hold": True}


@app.post("/_debug/unknown_recover")
async def debug_unknown_recover() -> dict[str, Any]:
    UNKNOWN_ONCE["hold"] = False
    for workflow in WORKFLOWS.values():
        if workflow["status"] == "outcome_unknown":
            workflow["status"] = "completed"
            RUNS[workflow["run_id"]]["status"] = "completed"
    return {"recovered": True}


@app.post("/_debug/release")
async def debug_release() -> dict[str, Any]:
    for event in RELEASE_EVENTS.values():
        event.set()
    return {"released": len(RELEASE_EVENTS)}


@app.post("/api/v1/app/runs", status_code=201)
async def create_run(body: RunBody, request: Request) -> Any:
    _record(request, body.model_dump_json())
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    _, external_user = authorized
    COUNTER["run"] += 1
    # Unique across stub restarts; the PA DB is durable while this process is not.
    run_id = f"run_stub_{uuid4().hex[:12]}"
    now = "2026-10-23T02:00:00Z"
    RUNS[run_id] = {
        "run_id": run_id,
        "external_user_id": external_user,
        "status": "pending",
        "created_at": now,
        "request": body.user_request,
    }
    return {
        "run_id": run_id,
        "user_id": "remote-owner",
        "session_id": None,
        "user_request": body.user_request,
        "run_type": "general",
        "status": "pending",
        "budget_limit": None,
        "cost_used": 0,
        "started_at": None,
        "completed_at": None,
        "created_at": now,
        "updated_at": now,
    }


@app.post("/api/v1/app/runs/{run_id}/agent-workflows")
async def start_workflow(run_id: str, body: WorkflowBody, request: Request) -> Any:
    _record(request, body.model_dump_json())
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    _, external_user = authorized
    run = RUNS.get(run_id)
    if run is None or run["external_user_id"] != external_user:
        return _error(404, "run_not_found", "运行不存在。")
    if (
        body.workflow_name != "general_agent"
        or body.workflow_version != "1.0.0"
        or body.execution_profile != "general_tools_v1"
        or "controller" not in body.role_bindings
    ):
        return _error(422, "invalid_request", "工作流定义不合法。")

    COUNTER["workflow"] += 1
    workflow_id = f"wf_stub_{uuid4().hex[:12]}"
    WORKFLOWS[workflow_id] = {
        "workflow_execution_id": workflow_id,
        "run_id": run_id,
        "status": "running",
        "events": _run_events(run_id, workflow_id),
    }
    run["status"] = "running"

    async def live_source():
        events = WORKFLOWS[workflow_id]["events"]
        for index, event in enumerate(events):
            if index == 1:
                await asyncio.sleep(0)
                if HOLD_MODE["enabled"]:
                    RELEASE_EVENTS.setdefault(workflow_id, asyncio.Event())
                    await RELEASE_EVENTS[workflow_id].wait()
            else:
                await asyncio.sleep(STREAM_DELAY)
            if (
                index == len(events) - 1
                and WORKFLOWS[workflow_id]["status"] != "cancelled"
                and UNKNOWN_ONCE["remaining"] > 0
            ):
                UNKNOWN_ONCE["remaining"] -= 1
                WORKFLOWS[workflow_id]["status"] = "outcome_unknown"
                run["status"] = "outcome_unknown"
                event = {
                    **event,
                    "type": "agent.workflow.outcome_unknown",
                    "payload": {**event["payload"], "workflow_status": "outcome_unknown"},
                }
            if index == len(events) - 1 and WORKFLOWS[workflow_id]["status"] == "cancelled":
                event = {
                    **event,
                    "type": "agent.workflow.cancelled",
                    "payload": {**event["payload"], "workflow_status": "cancelled"},
                }
            yield _frame(event)
            if index == len(events) - 1:
                if WORKFLOWS[workflow_id]["status"] not in {"cancelled", "outcome_unknown"}:
                    WORKFLOWS[workflow_id]["status"] = "completed"
                    run["status"] = "completed"

    if not body.stream:
        return _workflow_result(workflow_id)
    return StreamingResponse(
        live_source(),
        status_code=201,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/app/agent-workflows/{workflow_id}/events")
async def stream_events(
    workflow_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int | None = None,
) -> Any:
    _record(request, "")
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    _, external_user = authorized
    workflow = WORKFLOWS.get(workflow_id)
    if workflow is None or RUNS[workflow["run_id"]]["external_user_id"] != external_user:
        return _error(404, "workflow_not_found", "工作流不存在。")

    resume = 0
    if last_event_id and last_event_id.isdigit():
        resume = int(last_event_id)
    elif after is not None:
        resume = after
    pending = [event for event in workflow["events"] if event["sequence"] > resume]

    async def replay():
        for event in pending:
            await asyncio.sleep(STREAM_DELAY)
            yield _frame(event)
        if workflow["status"] != "cancelled":
            workflow["status"] = "completed"
            RUNS[workflow["run_id"]]["status"] = "completed"

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/app/agent-workflows/{workflow_id}")
async def workflow_summary(workflow_id: str, request: Request) -> Any:
    _record(request, "")
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    _, external_user = authorized
    workflow = WORKFLOWS.get(workflow_id)
    if workflow is None or RUNS[workflow["run_id"]]["external_user_id"] != external_user:
        return _error(404, "workflow_not_found", "工作流不存在。")
    return {
        "workflow_execution_id": workflow_id,
        "run_id": workflow["run_id"],
        "workflow_name": "general_agent",
        "workflow_version": "1.0.0",
        "execution_profile": "general_tools_v1",
        "status": workflow["status"],
    }


@app.get("/api/v1/app/agent-workflows/{workflow_id}/result")
async def workflow_result(workflow_id: str, request: Request) -> Any:
    _record(request, "")
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    _, external_user = authorized
    workflow = WORKFLOWS.get(workflow_id)
    if workflow is None or RUNS[workflow["run_id"]]["external_user_id"] != external_user:
        return _error(404, "workflow_not_found", "工作流不存在。")
    if workflow["status"] == "running":
        return _error(409, "workflow_not_completed", "工作流尚未结束。")
    if workflow["status"] == "outcome_unknown":
        if UNKNOWN_ONCE["hold"]:
            return _workflow_result(workflow_id)
        # First post-unknown read stays unknown; the next read reconciles to success.
        workflow["unknown_reads"] = workflow.get("unknown_reads", 0) + 1
        if workflow["unknown_reads"] >= 3:
            workflow["status"] = "completed"
            RUNS[workflow["run_id"]]["status"] = "completed"
    return _workflow_result(workflow_id)


def _workflow_result(workflow_id: str) -> dict[str, Any]:
    workflow = WORKFLOWS[workflow_id]
    return {
        "workflow_execution_id": workflow_id,
        "run_id": workflow["run_id"],
        "workflow_name": "general_agent",
        "workflow_version": "1.0.0",
        "execution_profile": "general_tools_v1",
        "status": workflow["status"],
        "final_output": (
            {"final_text": FINAL_TEXT} if workflow["status"] == "completed" else None
        ),
        "error": None,
    }


@app.post("/api/v1/app/agent-workflows/{workflow_id}/cancel")
async def cancel_workflow(workflow_id: str, request: Request) -> Any:
    _record(request, "")
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    _, external_user = authorized
    workflow = WORKFLOWS.get(workflow_id)
    if workflow is None or RUNS[workflow["run_id"]]["external_user_id"] != external_user:
        return _error(404, "workflow_not_found", "工作流不存在。")
    workflow["status"] = "cancelled"
    RUNS[workflow["run_id"]]["status"] = "cancelled"
    return {
        "workflow_execution_id": workflow_id,
        "run_id": workflow["run_id"],
        "status": "cancelled",
    }


@app.post("/api/v1/app/memory-retrievals")
async def memory_retrieval(body: MemoryBody, request: Request) -> Any:
    """Legacy memory proxy retained outside this task's App Run scope."""
    _record(request, body.model_dump_json())
    authorized = await _authorize(request)
    if isinstance(authorized, JSONResponse):
        return authorized
    return {
        "items": [
            {
                "memory_id": "mem_stub_1",
                "memory_type": "user_preference",
                "content": "用户偏好提前一天收到日程提醒",
                "source_reference": "conversation_stub/message_3",
                "score": 4.2,
            },
            {
                "memory_id": "mem_stub_2",
                "memory_type": "user_preference",
                "content": "这家影院空调冷，下次带件薄外套",
                "source_reference": "conversation_stub/message_9",
                "score": 3.1,
            },
        ][: body.limit],
        "next_cursor": None,
    }
