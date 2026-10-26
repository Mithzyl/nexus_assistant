"""Reserved iOS sync endpoints (snapshot upload and pending write-queue read)."""

from __future__ import annotations

from fastapi import APIRouter

from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.schemas import matters as schemas
from assistant_api.services import ios_sync

router = APIRouter(prefix="/api/v1/sync/ios", tags=["sync-ios"])


@router.post("/snapshot", response_model=schemas.IosSnapshotResponse)
async def upload_snapshot(
    payload: schemas.IosSnapshotRequest, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await ios_sync.apply_snapshot(db, user, payload)


@router.get("/pending-actions", response_model=schemas.PendingActionsResponse)
async def pending_actions(db: DatabaseSession, user: CurrentUser) -> dict:
    return await ios_sync.pending_actions(db, user)


__all__ = ["router"]
