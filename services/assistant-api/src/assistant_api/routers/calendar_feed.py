"""Private calendar subscription management and public tokenized ICS feed."""

from fastapi import APIRouter, Request, Response

from assistant_api.core.config import Settings
from assistant_api.core.deps import CurrentUser, DatabaseSession, SettingsDependency
from assistant_api.schemas import calendar as schemas
from assistant_api.services import calendar_feed

router = APIRouter(prefix="/api/v1/calendar-subscription", tags=["calendar-subscription"])
feed_router = APIRouter(prefix="/api/v1/calendar", tags=["calendar-subscription"])


def _feed_url(request: Request, settings: Settings, token: str) -> str:
    base = (settings.calendar_public_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/api/v1/calendar/{token}.ics"


@router.get("", response_model=schemas.CalendarSubscriptionStatus)
async def get_subscription(
    db: DatabaseSession, user: CurrentUser
) -> dict:
    return await calendar_feed.get_subscription(db, user)


@router.post("", response_model=schemas.CalendarSubscriptionRead, status_code=201)
async def create_subscription(
    payload: schemas.CalendarSubscriptionCreate,
    request: Request,
    db: DatabaseSession,
    user: CurrentUser,
    settings: SettingsDependency,
) -> dict:
    created = await calendar_feed.create_subscription(db, user, payload.matter_ids)
    return {
        **created["subscription"],
        "feed_url": _feed_url(request, settings, created["token"]),
        "link_returned_once": True,
    }


@router.post("/rotate", response_model=schemas.CalendarSubscriptionRead)
async def rotate_subscription(
    payload: schemas.CalendarSubscriptionCreate,
    request: Request,
    db: DatabaseSession,
    user: CurrentUser,
    settings: SettingsDependency,
) -> dict:
    rotated = await calendar_feed.rotate_subscription(db, user, payload.matter_ids)
    return {
        **rotated["subscription"],
        "feed_url": _feed_url(request, settings, rotated["token"]),
        "link_returned_once": True,
    }


@router.patch("", response_model=schemas.CalendarSubscriptionRead)
async def update_subscription(
    payload: schemas.CalendarSubscriptionUpdate,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    return await calendar_feed.update_subscription(
        db,
        user,
        enabled=payload.enabled,
        matter_ids=payload.matter_ids,
    )


@router.delete("", response_model=schemas.CalendarSubscriptionRead)
async def revoke_subscription(
    db: DatabaseSession, user: CurrentUser
) -> dict:
    return await calendar_feed.revoke_subscription(db, user)


@feed_router.get("/{token}.ics")
async def calendar_feed_response(
    token: str, db: DatabaseSession
) -> Response:
    body, calendar_name = await calendar_feed.read_feed(db, token)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Cache-Control": "private, no-cache",
            "Content-Disposition": 'inline; filename="personal-assistant.ics"',
        },
    )
