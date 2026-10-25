"""User-facing local-content source, authorization and upload endpoints."""

from typing import Literal

from fastapi import APIRouter, Query, status

from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.schemas.data_sources import (
    ContentItemRead,
    ContentListResponse,
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    SyncRequest,
    SyncResponse,
)
from assistant_api.services import data_source_service as service

router = APIRouter(prefix="/api/v1", tags=["data-sources"])
content_router = APIRouter(prefix="/api/v1", tags=["content"])


@router.post(
    "/data-sources", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED
)
async def create_data_source(
    payload: DataSourceCreate, user: CurrentUser, db: DatabaseSession
) -> DataSourceRead:
    return await service.create_source(db, user.user_id, payload)


@router.get("/data-sources", response_model=list[DataSourceRead])
async def list_data_sources(user: CurrentUser, db: DatabaseSession) -> list[DataSourceRead]:
    return await service.list_sources(db, user.user_id)


@router.get("/data-sources/{source_id}", response_model=DataSourceRead)
async def get_data_source(
    source_id: str, user: CurrentUser, db: DatabaseSession
) -> DataSourceRead:
    source = await service.get_source(db, user.user_id, source_id)
    return await service._to_source_read(db, source)


@router.patch("/data-sources/{source_id}", response_model=DataSourceRead)
async def update_data_source(
    source_id: str, payload: DataSourceUpdate, user: CurrentUser, db: DatabaseSession
) -> DataSourceRead:
    """Update sync, assistant authorization, fields and selected scope separately."""

    return await service.update_source(db, user.user_id, source_id, payload)


@router.post("/data-sources/{source_id}/sync", response_model=SyncResponse)
async def sync_data_source(
    source_id: str, payload: SyncRequest, user: CurrentUser, db: DatabaseSession
) -> SyncResponse:
    return await service.sync_source(db, user.user_id, source_id, payload)


@router.delete("/data-sources/{source_id}/items")
async def delete_uploaded_items(
    source_id: str, user: CurrentUser, db: DatabaseSession
) -> dict[str, int]:
    """Delete uploaded copies but keep the source registration and permissions."""
    return {"deleted": await service.delete_items(db, user.user_id, source_id)}


@router.delete("/data-sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_source(
    source_id: str, user: CurrentUser, db: DatabaseSession
) -> None:
    await service.delete_source(db, user.user_id, source_id)


@content_router.get("/content", response_model=ContentListResponse)
async def list_content(
    user: CurrentUser,
    db: DatabaseSession,
    kind: Literal["calendar_event", "reminder", "note"] | None = None,
    query: str | None = Query(default=None, max_length=1000),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
) -> ContentListResponse:
    return await service.list_content(
        db, user.user_id, kind=kind, query=query, limit=limit, offset=offset
    )


@content_router.get("/content/{item_id}", response_model=ContentItemRead)
async def get_content(
    item_id: str, user: CurrentUser, db: DatabaseSession
) -> ContentItemRead:
    return await service.get_content(db, user.user_id, item_id)
