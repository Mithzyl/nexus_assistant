"""Authenticated product-memory operations; no local persistence or identity override."""

from typing import Literal
from urllib.parse import urlencode

from assistant_api.nexus_client.typed_memory import TypedMemory

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
    local_run_id: str | None = Field(default=None, min_length=36, max_length=36)
    action: Literal["save", "propose", "correct", "forget", "confirm", "disclose", "settings", "undo"]
    key: str = Field(min_length=1, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    entry_id: str | None = Field(default=None, min_length=36, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    sensitive: bool = False
    confirmed_sensitive: bool = False
    app_data: AppMemoryData | None = None
    typed_data: TypedMemory | None = None
    confirmed_type: bool | None = None

    @model_validator(mode="after")
    def action_fields(self):
        """Require exact targets and reserve disclosure for an owned Run."""
        if self.action == "disclose" and not self.local_run_id:
            raise ValueError("Disclosure requires an explicit Run")
        if self.action in {"save", "propose", "correct"} and not (self.text or "").strip():
            raise ValueError("Text is required")
        if self.action in {"correct", "forget", "confirm", "disclose", "undo"} and (
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
    """Read current user data without a Run unless one was explicitly selected."""
    run_id = await resolve_memory_run(db, user.user_id, local_run_id) if local_run_id else None
    return await client.list_product_memory(user.user_id, run_id, offset=offset, limit=limit)


@router.get("/settings")
async def memory_settings(
    db: DatabaseSession, user: CurrentUser, client: NexusClient,
    local_run_id: str | None = Query(default=None, min_length=36, max_length=36),
):
    """Read current user data without a Run unless one was explicitly selected."""
    run_id = await resolve_memory_run(db, user.user_id, local_run_id) if local_run_id else None
    return await client.product_memory_settings(user.user_id, run_id)


@router.post("/commands")
async def memory_command(
    payload: MemoryCommand, db: DatabaseSession, user: CurrentUser, client: NexusClient,
):
    """Apply an explicit user command without manufacturing confirmation or identity."""
    run_id = await resolve_memory_run(db, user.user_id, payload.local_run_id) if payload.local_run_id else None
    command = payload.model_dump(mode="json", exclude_none=True, exclude={"local_run_id"})
    return await client.mutate_product_memory(user.user_id, run_id, {**command, "scope": "personal"})


class InstructionCommand(BaseModel):
    """Only this App entry can be edited, with no receiving-entry grants."""
    model_config = ConfigDict(extra="forbid")
    scope: Literal["entry"] = "entry"
    text: str = Field(min_length=1, max_length=4000)
    enabled: bool
    expected_version: int = Field(ge=0)
    allowed_entries: list[str] = Field(default_factory=list, max_length=0)


@router.get("/instructions")
async def read_instructions(user: CurrentUser, client: NexusClient):
    """Read this authenticated App user's instruction version."""
    return await client._get("/api/v1/app/me/instructions?scope=entry", external_user_id=user.user_id)


@router.post("/instructions")
async def write_instructions(payload: InstructionCommand, user: CurrentUser, client: NexusClient):
    """Save preferences for future Runs; Nexus checks the current version."""
    return await client._post("/api/v1/app/me/instructions", payload.model_dump(), external_user_id=user.user_id)


class ShareCommand(BaseModel):
    """Grant only explicitly selected fields to another linked entry."""
    model_config = ConfigDict(extra="forbid")
    entry_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)
    target_entry: Literal["native", "retail"]
    fields: list[str] = Field(min_length=1, max_length=32)
    automatic: bool = False


class RevokeCommand(BaseModel):
    """Use the grant version, independently of the memory version."""
    model_config = ConfigDict(extra="forbid")
    grant_id: str = Field(min_length=36, max_length=36)
    expected_version: int = Field(ge=1)


class SharedDisclosureCommand(RevokeCommand):
    """Authorize a specific locally owned Run to use shared sensitive fields."""
    local_run_id: str = Field(min_length=36, max_length=36)
    confirmed: Literal[True]
    key: str = Field(min_length=1, max_length=128)


@router.get("/shares")
async def list_shares(user: CurrentUser, client: NexusClient, entry_id: str = Query(min_length=36, max_length=36)):
    """Read current grants only after Nexus checks source ownership."""
    return await client._get("/api/v1/app/me/memory-shares?" + urlencode({"entry_id": entry_id}), external_user_id=user.user_id)


@router.post("/shares")
async def create_share(payload: ShareCommand, user: CurrentUser, client: NexusClient):
    """Forward a user's explicit projection without returning ungranted source content."""
    return await client._post("/api/v1/app/me/memory-shares", payload.model_dump(), external_user_id=user.user_id)


@router.post("/shares/revoke")
async def revoke_share(payload: RevokeCommand, user: CurrentUser, client: NexusClient):
    """Revoke one current grant without changing source memory."""
    return await client._post("/api/v1/app/me/memory-shares/revoke", payload.model_dump(), external_user_id=user.user_id)


@router.get("/shared")
async def list_shared(user: CurrentUser, client: NexusClient,
                      offset: int = Query(0, ge=0, le=10000), limit: int = Query(20, ge=1, le=50)):
    """Return only Nexus-authorized projections with their independent pagination."""
    return await client._get(f"/api/v1/app/me/shared-memories?offset={offset}&limit={limit}", external_user_id=user.user_id)


@router.post("/shared-disclosures")
async def disclose_shared(payload: SharedDisclosureCommand, db: DatabaseSession, user: CurrentUser, client: NexusClient):
    """Resolve local Run ownership before approving any shared sensitive disclosure."""
    run_id = await resolve_memory_run(db, user.user_id, payload.local_run_id)
    return await client._post(f"/api/v1/app/runs/{run_id}/shared-memory-disclosures",
                             payload.model_dump(exclude={"local_run_id"}), external_user_id=user.user_id)
