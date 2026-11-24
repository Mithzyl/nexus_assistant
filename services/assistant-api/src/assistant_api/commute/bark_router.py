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


@router.get("", response_model=BarkStatusRead,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '通知设置由设置页面管理。'}}
)
async def bark_status(request: Request, user: CurrentUser, db: DatabaseSession) -> BarkStatusRead:
    """Read notification switches and whether Web links are available."""
    return await service.status(db, user.user_id, web_origin=request.app.state.settings.bark_web_origin)


@router.post("/binding", response_model=BarkStatusRead, status_code=status.HTTP_201_CREATED,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '设备密钥不交给AI。'}}
)
async def bind_bark(
    payload: BarkBindingWrite, request: Request, user: CurrentUser, db: DatabaseSession
) -> BarkStatusRead:
    """Save the caller's key; this never sends or enables timed reminders."""
    return await service.bind(db, user.user_id, payload.device_key,
                              web_origin=request.app.state.settings.bark_web_origin)


@router.delete("/binding", response_model=BarkStatusRead,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '设备绑定由设置页面管理。'}}
)
async def unbind_bark(request: Request, user: CurrentUser, db: DatabaseSession) -> BarkStatusRead:
    """Remove the caller's Bark device and turn off both notification switches."""
    return await service.unbind(db, user.user_id, web_origin=request.app.state.settings.bark_web_origin)


@router.patch("", response_model=BarkStatusRead,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '通知授权由设置页面管理。'}}
)
async def update_bark_setting(
    payload: BarkSettingUpdate, request: Request, user: CurrentUser, db: DatabaseSession
) -> BarkStatusRead:
    """Change the caller's commute or Assistant notification switch."""
    return await service.update_settings(
        db, user.user_id, auto_reminders_enabled=payload.auto_reminders_enabled,
        assistant_notifications_enabled=payload.assistant_notifications_enabled,
        web_origin=request.app.state.settings.bark_web_origin,
    )


@router.post("/test", response_model=BarkTestRead,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '通知测试不是普通业务操作。'}}
)
async def send_bark_test(
    payload: BarkTestRequest, request: Request, user: CurrentUser, db: DatabaseSession
) -> BarkTestRead:
    """Send a test notification only after explicit confirmation."""
    return await service.send_test(db, user.user_id, request.app.state.bark_transport,
                                   web_origin=request.app.state.settings.bark_web_origin)
