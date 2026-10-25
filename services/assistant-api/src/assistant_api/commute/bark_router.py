"""Per-account Bark binding and explicit test API. Keys are never returned."""

from fastapi import APIRouter, Request, status

from assistant_api.commute.services import bark as service
from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.schemas.bark import (
    BarkBindingWrite,
    BarkSettingUpdate,
    BarkStatusRead,
    BarkTestRead,
    BarkTestRequest,
)

router = APIRouter(prefix="/api/v1/commute/bark", tags=["commute"])


@router.get("", response_model=BarkStatusRead)
async def bark_status(user: CurrentUser, db: DatabaseSession) -> BarkStatusRead:
    return await service.status(db, user.user_id)


@router.post("/binding", response_model=BarkStatusRead, status_code=status.HTTP_201_CREATED)
async def bind_bark(
    payload: BarkBindingWrite, user: CurrentUser, db: DatabaseSession
) -> BarkStatusRead:
    """Save the caller's key; this never sends or enables timed reminders."""
    return await service.bind(db, user.user_id, payload.device_key)


@router.delete("/binding", response_model=BarkStatusRead)
async def unbind_bark(user: CurrentUser, db: DatabaseSession) -> BarkStatusRead:
    return await service.unbind(db, user.user_id)


@router.patch("", response_model=BarkStatusRead)
async def update_bark_setting(
    payload: BarkSettingUpdate, user: CurrentUser, db: DatabaseSession
) -> BarkStatusRead:
    return await service.set_auto_reminders(db, user.user_id, payload.auto_reminders_enabled)


@router.post("/test", response_model=BarkTestRead)
async def send_bark_test(
    payload: BarkTestRequest, request: Request, user: CurrentUser, db: DatabaseSession
) -> BarkTestRead:
    return await service.send_test(db, user.user_id, request.app.state.bark_transport)
