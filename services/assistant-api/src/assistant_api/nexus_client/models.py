"""Request and response contracts for the current NexusPilot App API.

The running NexusPilot server is the source of truth for this module. It uses
``X-Application-Id`` plus ``X-External-User-Id`` and creates the application
user while authenticating the run. There is no principal-resolution endpoint
and no caller-supplied user id in any request body.

Provider/model bindings are an explicit current API requirement. They live in
this service's deployment configuration and are never exposed to clients.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RemoteRunStatus = Literal[
    "pending", "running", "completed", "failed", "cancel_requested", "cancelled"
]
RemoteWorkflowStatus = Literal[
    "pending", "running", "waiting_for_input", "completed", "failed",
    "cancelled", "outcome_unknown",
]


class StrictRequest(BaseModel):
    """Reject unknown fields in upstream request bodies."""

    model_config = ConfigDict(extra="forbid")


class UpstreamModel(BaseModel):
    """Parse only the fields we need; NexusPilot may return richer projections."""

    model_config = ConfigDict(extra="ignore")


class RoleBinding(StrictRequest):
    """One server-configured model binding for a general-agent role."""

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)


class RunCreateRequest(StrictRequest):
    """Current App Run creation accepts only the objective."""

    user_request: str = Field(min_length=1, max_length=100_000)


class RunView(UpstreamModel):
    """The subset of RunRead needed locally; the remote principal id is ignored."""

    run_id: str
    status: RemoteRunStatus
    created_at: datetime | None = None


class WorkflowCreateRequest(StrictRequest):
    """Exact current body for POST /api/v1/app/runs/{run_id}/agent-workflows."""

    workflow_name: Literal["general_agent"] = "general_agent"
    workflow_version: Literal["1.0.0"] = "1.0.0"
    execution_profile: Literal["general_tools_v1"] = "general_tools_v1"
    idempotency_key: str = Field(min_length=8, max_length=128)
    role_bindings: dict[str, RoleBinding]
    review_policy: Literal["always", "on_verification_failure", "never"] = "never"
    stream: bool = True


class WorkflowView(UpstreamModel):
    """Workflow identity and status; other summary fields are deliberately ignored."""

    workflow_execution_id: str
    run_id: str
    status: str
    completed_at: datetime | None = None


class WorkflowResultView(UpstreamModel):
    """Terminal result, including the public final text when one exists."""

    workflow_execution_id: str
    run_id: str
    status: RemoteWorkflowStatus
    final_output: dict | None = None
    error: dict | None = None


class MemoryQueryRequest(StrictRequest):
    """Legacy memory contract retained for the existing /memory/search proxy."""

    query_text: str = Field(min_length=1, max_length=2_000)
    memory_types: list[str] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=20, ge=1, le=100)
    token_budget: int = Field(default=2_000, ge=1, le=20_000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class MemoryHit(UpstreamModel):
    memory_id: str
    memory_type: str
    content: str
    source_reference: str | None = None
    score: float | None = None


class MemoryQueryResponse(UpstreamModel):
    items: list[MemoryHit] = Field(default_factory=list)
    next_cursor: str | None = None


class SseEvent(StrictRequest):
    """One parsed server-sent event with the identifiers needed to resume."""

    id: str | None = None
    event: str = "message"
    data: str
