"""Authenticated product-memory operations; no local persistence or identity override."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from assistant_api.core.deps import CurrentUser, DatabaseSession, NexusClient
from assistant_api.services.memory_service import resolve_memory_run

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


class AppMemoryData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["personal_assistant"] = "personal_assistant"
    schema_version: Literal[1] = 1
    fields: dict[str, JsonValue]


class MemoryCommand(BaseModel):
    """Only an explicit authenticated user action can confirm sensitive content."""

    model_config = ConfigDict(extra="forbid")
    local_run_id: str = Field(min_length=36, max_length=36)
    action: Literal["save", "propose", "correct", "forget", "confirm", "disclose", "settings"]
    key: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    entry_id: str | None = Field(default=None, min_length=36, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    sensitive: bool = False
    confirmed_sensitive: bool = False
    app_data: AppMemoryData | None = None

    @model_validator(mode="after")
    def action_fields(self):
        if self.action in {"save", "propose", "correct"} and not (self.text or "").strip():
            raise ValueError("Text is required")
        if self.action in {"correct", "forget", "confirm", "disclose"} and (
            not self.entry_id or self.expected_version is None or self.expected_version < 1
        ):
            raise ValueError("Target and current version are required")
        if self.action == "settings" and (self.expected_version is None or self.enabled is None):
            raise ValueError("Setting and current version are required")
        return self


@router.get("")
async def list_memory(
    db: DatabaseSession, user: CurrentUser, client: NexusClient,
    local_run_id: str | None = Query(default=None, min_length=36, max_length=36),
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=20, ge=1, le=50),
):
    run_id = await resolve_memory_run(db, user.user_id, local_run_id)
    return await client.list_product_memory(user.user_id, run_id, offset=offset, limit=limit)


@router.get("/settings")
async def memory_settings(
    db: DatabaseSession, user: CurrentUser, client: NexusClient,
    local_run_id: str | None = Query(default=None, min_length=36, max_length=36),
):
    run_id = await resolve_memory_run(db, user.user_id, local_run_id)
    return await client.product_memory_settings(user.user_id, run_id)


@router.post("/commands")
async def memory_command(
    payload: MemoryCommand, db: DatabaseSession, user: CurrentUser, client: NexusClient,
):
    run_id = await resolve_memory_run(db, user.user_id, payload.local_run_id)
    command = payload.model_dump(mode="json", exclude_none=True, exclude={"local_run_id"})
    return await client.mutate_product_memory(user.user_id, run_id, {**command, "scope": "personal"})
