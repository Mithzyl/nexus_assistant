"""Bounded, authenticated endpoint for complete Nexus main-Agent bodies."""
from typing import Literal

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant_api.core.deps import DatabaseSession, SettingsDependency
from assistant_api.routers.internal_tools import _require_callback_token
from assistant_api.services.agent_responses import process_response

router = APIRouter(prefix='/internal/nexus', tags=['internal-tools'])


class AgentResponseEnvelope(BaseModel):
    """Identity is asserted by Nexus; content never selects the actor or service."""
    model_config = ConfigDict(extra='forbid')
    protocol_version: Literal['1']
    application_id: str = Field(min_length=1, max_length=64)
    external_user_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    workflow_id: str = Field(min_length=1, max_length=128)
    response_id: str = Field(min_length=1, max_length=128)
    execution_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=1048576)


@router.post('/agent-responses')
async def agent_response(request: Request, db: DatabaseSession, settings: SettingsDependency) -> dict:
    """Read at most 1 MiB, then execute and return one durable processing result."""
    _require_callback_token(request, settings)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 1048576:
            raise HTTPException(413, 'Callback exceeds 1 MiB')
    try:
        payload = AgentResponseEnvelope.model_validate_json(data)
    except ValidationError as exc:
        raise HTTPException(422, 'Invalid caller envelope') from exc
    return await process_response(db, request.app, payload.model_dump())
