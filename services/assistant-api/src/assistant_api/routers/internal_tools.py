"""Read-only business tool callbacks from the current NexusPilot App runtime.

The current envelope is a single JSON object authenticated by the application
service token:

    {"tool_execution_id","application_id","external_user_id",
     "tool_name","tool_version","arguments"}

Identity comes from the authenticated Run on the NexusPilot side; it is never
taken from model arguments. A repeated tool_execution_id returns the stored
response and does not execute the business read again.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from assistant_api.core.deps import DatabaseSession, SettingsDependency
from assistant_api.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthenticatedError,
)
from assistant_api.models import ToolCallReceipt, User
from assistant_api.services.tool_service import execute_tool, tool_definitions

router = APIRouter(prefix="/internal/nexus/tools", tags=["internal-tools"])
SUPPORTED_TOOL_VERSION = "1.0.0"


def _arguments_digest(arguments: dict[str, Any]) -> str:
    """Bind an execution id to one canonical argument object."""

    canonical = json.dumps(
        arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolCallEnvelope(BaseModel):
    """Current NexusPilot RemoteToolAdapter body; no owner/model fields are trusted."""

    model_config = ConfigDict(extra="forbid")

    tool_execution_id: str = Field(min_length=1, max_length=128)
    application_id: str = Field(min_length=1, max_length=64)
    external_user_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: str = Field(min_length=1, max_length=32)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolDefinitionList(BaseModel):
    """Describe the read-only tool set this application answers."""

    tools: list[dict[str, Any]]


def _require_callback_token(request: Request, settings: SettingsDependency) -> None:
    """Verify the application service token with a constant-time digest comparison."""

    header = request.headers.get("authorization") or ""
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise UnauthenticatedError("缺少应用调用凭证。")
    expected = hashlib.sha256(
        settings.tool_callback_token.get_secret_value().encode("utf-8")
    ).hexdigest()
    actual = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise UnauthenticatedError("应用调用凭证无效。")


@router.get("", response_model=ToolDefinitionList)
async def list_tools(
    request: Request, settings: SettingsDependency
) -> ToolDefinitionList:
    """Expose the registrable read-only tool set to the platform administrator."""

    _require_callback_token(request, settings)
    return ToolDefinitionList(tools=tool_definitions())


@router.post("")
async def call_tool(
    payload: ToolCallEnvelope,
    request: Request,
    response: Response,
    db: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    """Execute one registered read-only operation inside the asserted user scope."""

    _require_callback_token(request, settings)
    if payload.application_id != settings.nexus_application_id:
        raise ForbiddenError("application_mismatch", "应用标识与凭据不匹配。")
    if payload.tool_version != SUPPORTED_TOOL_VERSION:
        raise ConflictError("tool_version_unsupported", "工具版本不受支持。")
    digest = _arguments_digest(payload.arguments)

    existing = await db.scalar(
        select(ToolCallReceipt).where(
            ToolCallReceipt.tool_call_id == payload.tool_execution_id
        )
    )
    if existing is not None:
        if (
            existing.external_user_id != payload.external_user_id
            or existing.tool_name != payload.tool_name
        ):
            raise ForbiddenError(
                "tool_call_identity_mismatch", "同一调用 ID 不能用于其他用户或工具。"
            )
        if (
            existing.arguments_sha256 is not None
            and existing.arguments_sha256 != digest
        ):
            raise ConflictError(
                "tool_call_arguments_mismatch",
                "同一调用 ID 不能用于不同的参数。",
            )
        if existing.arguments_sha256 is None:
            # Upgrade a legacy receipt on its first replay; it still returns the
            # original response, but future mismatches are rejected.
            existing.arguments_sha256 = digest
            await db.commit()
        response.headers["X-Idempotent-Replay"] = "true"
        return existing.response_json

    user = await db.scalar(
        select(User).where(
            User.user_id == payload.external_user_id, User.status == "active"
        )
    )
    if user is None:
        raise NotFoundError("external_user_unknown", "用户不存在。")

    result = await execute_tool(
        db,
        user_id=user.user_id,
        tool_name=payload.tool_name,
        arguments=payload.arguments,
        settings=settings,
    )

    db.add(
        ToolCallReceipt(
            tool_call_id=payload.tool_execution_id,
            tool_name=payload.tool_name,
            external_user_id=payload.external_user_id,
            nexus_run_id=None,
            status="completed",
            arguments_sha256=digest,
            response_json=result,
        )
    )
    await db.commit()
    response.headers["X-Idempotent-Replay"] = "false"
    return result


@router.get("/{tool_name}/schema", status_code=status.HTTP_200_OK)
async def tool_schema(
    tool_name: str, request: Request, settings: SettingsDependency
) -> dict[str, Any]:
    """Return one tool's pinned schema without executing it."""

    _require_callback_token(request, settings)
    for definition in tool_definitions():
        if definition["name"] == tool_name:
            return definition
    raise NotFoundError("unknown_tool", "工具不存在。")
