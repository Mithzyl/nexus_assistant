"""Reserved iOS sync endpoints (snapshot upload and pending write-queue read)."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from assistant_api.core.deps import CurrentUser, DatabaseSession, get_current_user
from assistant_api.core.errors import ForbiddenError
from assistant_api.schemas import matters as schemas
from assistant_api.schemas import sync as shortcut_schemas
from assistant_api.services import ios_sync, shortcut_auth

router = APIRouter(prefix="/api/v1/sync/ios", tags=["sync-ios"])


@router.post("/snapshot", response_model=schemas.IosSnapshotResponse)
async def upload_snapshot(
    payload: schemas.IosSnapshotRequest,
    request: Request,
    db: DatabaseSession,
    x_shortcut_token: str | None = Header(
        default=None,
        alias="X-Shortcut-Token",
        min_length=20,
        max_length=128,
    ),
) -> dict:
    """Accept either a normal user session or one source-scoped Shortcut token."""

    if x_shortcut_token:
        user, credential = await shortcut_auth.resolve_token(db, x_shortcut_token)
        if credential.source_id != payload.source_id:
            raise ForbiddenError(
                "shortcut_token_source_mismatch",
                "该设备上传凭据不能写入其他资料来源。",
            )
    else:
        user = await get_current_user(request, db)
    result = await ios_sync.apply_snapshot(db, user, payload)
    await db.commit()
    return result


@router.post(
    "/shortcut-tokens",
    response_model=shortcut_schemas.ShortcutTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_shortcut_token(
    payload: shortcut_schemas.ShortcutTokenCreate,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    return await shortcut_auth.create_token(
        db,
        user,
        source_id=payload.source_id,
        display_name=payload.display_name,
    )


@router.get("/shortcut-tokens", response_model=list[shortcut_schemas.ShortcutTokenRead])
async def list_shortcut_tokens(
    db: DatabaseSession, user: CurrentUser
) -> list[dict]:
    return await shortcut_auth.list_tokens(db, user)


@router.delete(
    "/shortcut-tokens/{token_id}",
    response_model=shortcut_schemas.ShortcutTokenRead,
)
async def revoke_shortcut_token(
    token_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    return await shortcut_auth.revoke_token(db, user, token_id)


@router.get("/pending-actions", response_model=schemas.PendingActionsResponse)
async def pending_actions(db: DatabaseSession, user: CurrentUser) -> dict:
    return await ios_sync.pending_actions(db, user)


__all__ = ["router"]
